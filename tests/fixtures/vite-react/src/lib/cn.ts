export function cn(...parts: Array<string | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
