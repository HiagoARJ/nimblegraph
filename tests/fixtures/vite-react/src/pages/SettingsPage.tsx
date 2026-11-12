import { createUser } from '../api/client'

export default function SettingsPage() {
  return <button onClick={() => createUser({ name: 'x' })}>Save</button>
}
