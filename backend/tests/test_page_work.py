"""Le assegnazioni non concedono permessi e le approvazioni non saltano la revisione."""
import pytest
from fastapi.testclient import TestClient
from app import config
from app.db import connect, init_db
from app.main import app
from app.services.users import create_user

PASSWORD = 'WorkFixture2026!'

@pytest.fixture()
def team(monkeypatch):
    monkeypatch.setattr(config, 'AUTH_MODE', 'on')
    init_db()
    with connect() as conn:
        conn.execute('DELETE FROM projects')
        conn.execute('DELETE FROM users')
        ids = {name: create_user(conn, name, PASSWORD, role=role)['id'] for name, role in [
            ('owner_work','editor'), ('annotator_work','editor'), ('reviewer_work','editor'), ('reader_work','viewer'), ('outsider_work','editor')]}
        pid = conn.execute("INSERT INTO projects(name,root_dir,owner_id) VALUES('Work','/tmp',?)", (ids['owner_work'],)).lastrowid
        for name in ['annotator_work','reviewer_work','reader_work']:
            conn.execute('INSERT INTO project_members(project_id,user_id,role) VALUES(?,?,?)', (pid, ids[name], 'viewer' if name=='reader_work' else 'editor'))
        page = conn.execute("INSERT INTO pages(project_id,rel_path,abs_path,width,height) VALUES(?,'page.png','/tmp/page.png',100,100)", (pid,)).lastrowid
        block = conn.execute("INSERT INTO blocks(page_id,label,points,content,confirmed) VALUES(?,'Text','[[0,0],[20,20]]','Aurora',1)", (page,)).lastrowid
    return dict(ids=ids, pid=pid, page=page, block=block)

def login(client, who):
    assert client.post('/api/auth/login', json={'username':who,'password':PASSWORD}).status_code == 200

def assign(client, team):
    login(client,'owner_work')
    response = client.put(f"/api/pages/{team['page']}/work",json={'expected_version':0, 'assignee_id':team['ids']['annotator_work'],'reviewer_id':team['ids']['reviewer_work']})
    assert response.status_code == 200, response.text
    return response.json()

def submit(client, team):
    login(client,'annotator_work')
    response = client.post(f"/api/pages/{team['page']}/work/submit",json={'expected_version':1,'annotation_revision':0})
    assert response.status_code == 200, response.text
    return response.json()

def test_assignment_permissions_and_eligible_members(team):
    with TestClient(app) as c:
        login(c,'annotator_work')
        url=f"/api/pages/{team['page']}/work"
        assert c.put(url,json={'expected_version':0}).status_code==403
        login(c,'owner_work')
        assert c.put(url,json={'expected_version':0,'assignee_id':team['ids']['annotator_work'],'reviewer_id':team['ids']['annotator_work']}).status_code==422
        assert c.put(url,json={'expected_version':0,'assignee_id':team['ids']['reader_work']}).status_code==422
        assert c.put(url,json={'expected_version':0,'assignee_id':team['ids']['outsider_work']}).status_code==422
        work=assign(c,team)
        assert work['version']==1
        assert c.put(url,json={'expected_version':0}).status_code==409
        login(c,'reader_work')
        assert c.get(url).status_code==200
        assert c.post(url+'/submit',json={'expected_version':1,'annotation_revision':0}).status_code==403
        login(c,'outsider_work')
        assert c.get(url).status_code==404

def test_handoff_request_changes_resubmit_and_approve(team):
    with TestClient(app) as c:
        assign(c,team); work=submit(c,team)
        assert work['state']=='pending'
        url=f"/api/pages/{team['page']}/work/review"
        body={'expected_version':2,'annotation_revision':0,'decision':'approved'}
        assert c.post(url,json=body).status_code==403  # non auto-approvazione
        login(c,'reviewer_work')
        assert c.post(url,json={**body,'decision':'changes_requested'}).status_code==422
        response=c.post(url,json={**body,'decision':'changes_requested','note':'Correggere Aurora'})
        assert response.status_code==200, response.text
        assert response.json()['state']=='changes_requested'
        login(c,'annotator_work')
        assert c.get(f"/api/projects/{team['pid']}/pages").json()['items'][0]['work']['note']=='Correggere Aurora'
        assert c.post(f"/api/pages/{team['page']}/work/submit",json={'expected_version':3,'annotation_revision':0}).status_code==200
        login(c,'reviewer_work')
        response=c.post(url,json={**body,'expected_version':4})
        assert response.status_code==200, response.text
        assert response.json()['state']=='approved'
        # Una correzione successiva invalida l'approvazione anche passando da un'API diversa.
        c.patch(f"/api/blocks/{team['block']}",json={'content':'Aurora corrected'})
        work=c.get(f"/api/pages/{team['page']}/work").json()
        assert work['state']=='changes_requested'
        assert c.get(f"/api/projects/{team['pid']}/pages").json()['items'][0]['status']=='review'

def test_legacy_approval_and_reviews_cannot_bypass_assigned_review(team):
    with TestClient(app) as c:
        assign(c,team); submit(c,team)
        assert c.post(f"/api/pages/{team['page']}/approve",json={'confirm_drafts':True}).status_code==409
        result=c.post(f"/api/projects/{team['pid']}/pages/approve",json={'confirm_drafts':True}).json()
        assert result['approved']==0 and result['skipped_count']==1
        assert c.post(f"/api/pages/{team['page']}/reviews",json={'reviewer':'reviewer_work','status':'pass'}).status_code==409

def test_review_checks_the_version_seen_and_preserves_readiness(team):
    with TestClient(app) as c:
        assign(c,team)
        login(c,'annotator_work')
        c.patch(f"/api/blocks/{team['block']}",json={'confirmed':False})
        assert c.post(f"/api/pages/{team['page']}/work/submit",json={'expected_version':1,'annotation_revision':1}).status_code==409
        c.patch(f"/api/blocks/{team['block']}",json={'confirmed':True})
        assert c.post(f"/api/pages/{team['page']}/work/submit",json={'expected_version':1,'annotation_revision':2}).status_code==200
        login(c,'reviewer_work')
        c.patch(f"/api/blocks/{team['block']}",json={'content':'Updated'})
        body={'expected_version':2,'annotation_revision':2,'decision':'approved'}
        assert c.post(f"/api/pages/{team['page']}/work/review",json=body).status_code==409
        assert c.post(f"/api/pages/{team['page']}/work/review",json={**body,'annotation_revision':3}).status_code==200

def test_stale_block_and_table_writes_roll_back_without_overwriting(team):
    with TestClient(app) as c:
        login(c,'annotator_work')
        block=f"/api/blocks/{team['block']}"
        assert c.patch(block+'?expected_revision=0',json={'content':'Current'}).status_code==200
        assert c.patch(block+'?expected_revision=0',json={'content':'Stale'}).status_code==409
        assert c.get(block).json()['content']=='Current'
        assert c.delete(block+'?expected_revision=0').status_code==409
        assert c.get(block).status_code==200
        c.patch(block+'?expected_revision=1',json={'label':'Table'})
        grid={'rows':1,'cols':1,'cells':[{'r':0,'c':0,'text':'Value','rowspan':1,'colspan':1}]}
        assert c.put(block+'/table?expected_revision=1',json=grid).status_code==409
        assert c.get(block+'/table').json()['grid'] is None
        assert c.put(block+'/table?expected_revision=2',json=grid).status_code==200
        assert c.get(block).json()['annotation_revision']==3
