# Teams, sharing and review

Tabularium is self-hosted and multi-user.

- **Accounts**: the first launch creates the instance administrator; login uses
  HttpOnly session cookies. Passwords and session tokens are stored only as
  hashes. Admins can invite people by link; invited users choose their own
  password, and accounts created or reset by an admin must change it at first
  login.
- **Roles**: global roles (admin / editor / viewer) plus per-project sharing.
  The project list shows owner, people and your effective access, and filters
  separate your own projects from shared ones.
- **Sharing**: **Share** opens a dialog where you pick a colleague, choose the
  permission and confirm. **Read only** opens scans and saved content without
  the editor; annotating or reviewing requires **Can edit**.
- **Assignments and review**: in the studio the owner or an administrator
  assigns a distinct annotator and reviewer per page. Submission, change
  requests and approval are recorded by the server, and an edit after approval
  invalidates it. The **To annotate** and **To review** filters show your
  personal queue.
- **Live presence**: people working on the same page see each other, and a page
  changed elsewhere is reloaded without losing unsaved work.

Behaviour, checks and known limits (in Italian):
[UX_STUDIO_REVIEW.md](https://github.com/nbadino/tabularium/blob/main/docs/UX_STUDIO_REVIEW.md),
[UX_TEAM_WORKSPACE.md](https://github.com/nbadino/tabularium/blob/main/docs/UX_TEAM_WORKSPACE.md).
