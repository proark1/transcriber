import { AuthProvider, useAuth } from "./auth/AuthProvider.tsx";
import { LoginPage } from "./auth/LoginPage.tsx";
import { AppShell } from "./layout/AppShell.tsx";

function AuthenticatedApp() {
  const auth = useAuth();

  if (auth.state === "loading") {
    return (
      <main className="centered-state" aria-live="polite">
        <span className="brand-tile" aria-hidden="true">
          T
        </span>
        <p>Opening your private workspace…</p>
      </main>
    );
  }

  if (auth.state === "signed-out") {
    return <LoginPage />;
  }

  return <AppShell />;
}

export function App() {
  return (
    <AuthProvider>
      <AuthenticatedApp />
    </AuthProvider>
  );
}
