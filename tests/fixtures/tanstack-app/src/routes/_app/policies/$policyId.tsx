import { createFileRoute } from '@tanstack/react-router'
import { PolicyDetail } from '@/features/policies/PolicyDetail'
export const Route = createFileRoute('/_app/policies/$policyId')({ component: PolicyDetail })
