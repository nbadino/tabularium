import { lazy, Suspense, useEffect } from 'react'
import type { ReactElement } from 'react'
import { Link, Route, Routes } from 'react-router'
import AuthGate from './app/AuthGate'
import Layout from './app/Layout'
import { useAuth } from './app/auth'
const HomePage = lazy(() => import('./pages/HomePage'))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'))
const ProjectDetailPage = lazy(() => import('./pages/ProjectDetailPage'))
const AnnotationPage = lazy(() => import('./pages/AnnotationPage'))
const DatasetPage = lazy(() => import('./pages/DatasetPage'))
const TrainingPage = lazy(() => import('./pages/TrainingPage'))
const EvaluationPage = lazy(() => import('./pages/EvaluationPage'))
const PlaygroundPage = lazy(() => import('./pages/PlaygroundPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const UsersPage = lazy(() => import('./pages/UsersPage'))
import { Module } from './app/ui'
import { useI18n } from './i18n'

/** Una rotta inesistente è uno stato, non uno schermo bianco. */
function NotFoundPage() {
  const { t } = useI18n()
  return (
    <div className="p-3">
      <Module tab={t('notFound.tab')}>
        <h1 className="text-[19px] font-bold tracking-[-0.02em]">
          {t('notFound.title')}
        </h1>
        <p className="mt-1 max-w-[60ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('notFound.body')}
        </p>
        <Link to="/" className="btn btn-primary mt-3 no-underline">
          {t('notFound.back')}
        </Link>
      </Module>
    </div>
  )
}

/** Tiene allineati lingua, titolo della finestra e attributo `lang`. */
function LocaleEffects() {
  const { locale, t } = useI18n()
  useEffect(() => {
    document.documentElement.lang = locale
    document.title = t('app.title')
  }, [locale, t])
  return null
}

/**
 * Le pagine di amministrazione non si aprono nemmeno per chi non è admin:
 * digitare l'URL a mano non basta, il backend rifiuterebbe comunque.
 */
function AdminRoute({ children }: { children: ReactElement }) {
  const { user } = useAuth()
  if (user?.role !== 'admin') return <NotFoundPage />
  return children
}

export default function App() {
  const { t } = useI18n()
  return (
    <Suspense
      fallback={
        <div className="p-6 text-sm text-[color:var(--color-ink-2)]">
          {t('app.loadingModule')}
        </div>
      }
    >
      <LocaleEffects />
      <AuthGate>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<HomePage />} />
            <Route path="progetti" element={<ProjectsPage />} />
            <Route path="progetti/:id" element={<ProjectDetailPage />} />
            <Route path="annotazione" element={<AnnotationPage />} />
            <Route path="dataset" element={<DatasetPage />} />
            <Route path="training" element={<TrainingPage />} />
            <Route path="valutazione" element={<EvaluationPage />} />
            <Route path="playground" element={<PlaygroundPage />} />
            <Route path="impostazioni" element={<SettingsPage />} />
            <Route
              path="utenti"
              element={
                <AdminRoute>
                  <UsersPage />
                </AdminRoute>
              }
            />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </AuthGate>
    </Suspense>
  )
}
