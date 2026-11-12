import { NextResponse } from 'next/server'
import { listUsers } from '@/lib/users'
export async function GET() { return NextResponse.json(await listUsers()) }
export async function POST(req: Request) { return NextResponse.json(await req.json(), { status: 201 }) }
