import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, createBrowserRouter } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import UsersPage from '@/pages/UsersPage'

const SettingsPage = lazy(() => import('./pages/SettingsPage'))

export const router = createBrowserRouter([
  { path: '/', element: <Layout />, children: [{ path: 'users', Component: UsersPage }] },
  { path: '/settings', lazy: () => import('./pages/SettingsPage') },
])

export function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<p>Don't panic, loading...</p>}>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route path="users" element={<UsersPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}
