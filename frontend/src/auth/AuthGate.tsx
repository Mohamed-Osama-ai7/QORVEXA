import { useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect } from 'react'
import { PublicLanding } from '../features/public/PublicLanding'
import { useAuth } from './AuthProvider'

function Centered({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-full max-w-md flex-col justify-center gap-4 p-6">
      {children}
    </main>
  )
}

/** Renders the application only for a signed-in user; clears cached data on sign-out. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { status, error, signIn } = useAuth()
  const queryClient = useQueryClient()

  useEffect(() => {
    if (status === 'signed-out' || status === 'error') queryClient.clear()
  }, [status, queryClient])

  if (status === 'signed-in') return <>{children}</>

  if (status === 'loading') {
    return (
      <Centered>
        <p role="status">Signing in…</p>
      </Centered>
    )
  }

  if (status === 'unconfigured') {
    return <PublicLanding status="unconfigured" />
  }

  return <PublicLanding status="signed-out" error={error} onSignIn={signIn} />
}
