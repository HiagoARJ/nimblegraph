'use client'
import { useEffect, useState } from 'react'
import type { User } from '@/lib/users'

export function UserTable() {
  const [rows, setRows] = useState<User[]>([])
  useEffect(() => { fetch('/api/users').then((r) => r.json()).then(setRows) }, [])
  return <table>{rows.map((r) => <tr key={r.id}><td>{r.name}</td></tr>)}</table>
}
