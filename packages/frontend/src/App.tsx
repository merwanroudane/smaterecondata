import { Suspense, lazy } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  BrowserRouter as Router,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";
import { AuthProvider } from "./contexts/AuthContext";
import { ThemeProvider } from "./contexts/ThemeContext";
import { CartProvider } from "./contexts/CartContext";
import { I18nProvider } from "./i18n";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { RouteMeta } from "./components/RouteMeta";

const queryClient = new QueryClient();
const HomePage = lazy(() =>
  import("./components/HomePage").then((module) => ({
    default: module.HomePage,
  })),
);
const LandingPage = lazy(() =>
  import("./components/LandingPage").then((module) => ({
    default: module.LandingPage,
  })),
);
const ChatPage = lazy(() =>
  import("./components/ChatPage").then((module) => ({
    default: module.ChatPage,
  })),
);
const DocsPage = lazy(() =>
  import("./pages/DocsPage").then((module) => ({ default: module.DocsPage })),
);
const ExamplesPage = lazy(() =>
  import("./pages/ExamplesPage").then((module) => ({
    default: module.ExamplesPage,
  })),
);
const AuthCallback = lazy(() =>
  import("./pages/AuthCallback").then((module) => ({
    default: module.AuthCallback,
  })),
);
const ResetPassword = lazy(() =>
  import("./pages/ResetPassword").then((module) => ({
    default: module.ResetPassword,
  })),
);

function RouteFallback() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "Inter, system-ui, sans-serif",
        color: "#64748b",
        backgroundColor: "#f8fafc",
      }}
    >
      Loading...
    </div>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <I18nProvider>
        <ThemeProvider>
          <CartProvider>
            <QueryClientProvider client={queryClient}>
              <AuthProvider>
                <Router>
                  <RouteMeta />
                  <Suspense fallback={<RouteFallback />}>
                    <Routes>
                      <Route path="/" element={<HomePage />} />
                      <Route path="/about" element={<LandingPage />} />
                      <Route path="/chat" element={<ChatPage />} />
                      <Route path="/docs" element={<DocsPage />} />
                      <Route path="/examples" element={<ExamplesPage />} />
                      <Route path="/auth/callback" element={<AuthCallback />} />
                      <Route
                        path="/reset-password"
                        element={<ResetPassword />}
                      />
                      {/* Catch-all: redirect unmatched/stale URLs to the landing page */}
                      <Route path="*" element={<Navigate to="/" replace />} />
                    </Routes>
                  </Suspense>
                </Router>
              </AuthProvider>
            </QueryClientProvider>
          </CartProvider>
        </ThemeProvider>
      </I18nProvider>
    </ErrorBoundary>
  );
}

export default App;
