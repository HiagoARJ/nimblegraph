import { usePolicies } from './api'
export function PoliciesPage() { const { data } = usePolicies(); return <ul>{data?.map((p) => <li key={p}>{p}</li>)}</ul> }
