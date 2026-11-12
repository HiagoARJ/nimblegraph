import { createFileRoute } from '@tanstack/react-router'
import { PoliciesPage } from '@/features/policies/PoliciesPage'
export const Route = createFileRoute('/_app/policies/')({ component: PoliciesPage })
