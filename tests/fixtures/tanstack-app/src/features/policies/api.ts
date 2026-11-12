import { http } from '@/lib/http'
export function usePolicies() { return { data: [] as string[], load: () => http.get('/portal/v1/policies') } }
