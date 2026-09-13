/** Application shell: navigation, routing and the error boundary. */
import React from "react";
import { api } from "./services/api";
import { useAsync, useHashRoute } from "./hooks/useAsync";
import { BlindSpotsPage } from "./pages/BlindSpotsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TestsPage } from "./pages/TestsPage";
import { Banner } from "./components/ui";

interface NavItem {
  key: string;
  label: string;
  count?: number;
}

export default function App() {
  const [route, navigate] = useHashRoute();
  const [section, param] = route.split("/");

  // Polled lightly so the sidebar counts stay honest after an analysis without
  // making every screen re-fetch the dashboard.
  const health = useAsync(() => api.dashboard(), [route]);
  const metrics = health.data?.metrics;

  const items: NavItem[] = [
    { key: "dashboard", label: "Dashboard" },
    { key: "tests", label: "Tests", count: metrics?.tests_indexed },
    { key: "incidents", label: "Incidents", count: metrics?.incidents },
    { key: "blind-spots", label: "Blind Spots", count: metrics?.blind_spots },
    { key: "settings", label: "Settings" },
  ];

  const externalAi = Boolean(health.data?.ai?.llm_active);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar__inner">
          <div className="brand">
            <div className="brand__name">BlindSpot</div>
            <div className="brand__tag">Test coverage intelligence</div>
          </div>

          <nav className="nav">
            {items.map((item) => (
              <button
                key={item.key}
                className={`nav__item${section === item.key ? " nav__item--active" : ""}`}
                onClick={() => navigate(item.key)}
              >
                <span>{item.label}</span>
                {item.count !== undefined ? (
                  <span className="nav__count">{item.count.toLocaleString()}</span>
                ) : null}
              </button>
            ))}
          </nav>

          <div className="sidebar__foot">
            {externalAi ? (
              <>External AI enabled, see Settings.</>
            ) : (
              <>Running fully local. No data leaves this machine.</>
            )}
          </div>
        </div>
      </aside>

      <main className="main">
        <ErrorBoundary key={route}>
          <Router section={section} param={param} navigate={navigate} />
        </ErrorBoundary>
      </main>
    </div>
  );
}

function Router({
  section,
  param,
  navigate,
}: {
  section: string;
  param?: string;
  navigate: (route: string) => void;
}) {
  switch (section) {
    case "tests":
      return <TestsPage />;
    case "incidents":
      return <IncidentsPage incidentId={param} navigate={navigate} />;
    case "blind-spots":
      return <BlindSpotsPage patternKey={param} navigate={navigate} />;
    case "settings":
      return <SettingsPage />;
    case "dashboard":
    case "":
      return <DashboardPage navigate={navigate} />;
    default:
      return (
        <Banner tone="warn">
          Unknown screen "{section}". <a href="#/dashboard">Return to the dashboard</a>.
        </Banner>
      );
  }
}

/** Keeps one broken screen from blanking the whole application. */
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("Screen crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="stack">
          <Banner tone="error">This screen failed to render: {this.state.error.message}</Banner>
          <button className="btn" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
