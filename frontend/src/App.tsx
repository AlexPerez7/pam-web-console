import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { lazy } from "react";
import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "@/components/AppShell";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";
import { useAuth } from "./lib/auth";
import Login from "./pages/Login";

// Cada página se descarga recién al navegar a ella (code-splitting por ruta):
// el login y el shell cargan sin traer las ~30 páginas de la app.
const Account = lazy(() => import("./pages/Account"));
const AccountBackups = lazy(() => import("./pages/AccountBackups"));
const Accounts = lazy(() => import("./pages/Accounts"));
const AleroActivities = lazy(() => import("./pages/AleroActivities"));
const AleroRoles = lazy(() => import("./pages/AleroRoles"));
const AleroUserDetail = lazy(() => import("./pages/AleroUserDetail"));
const AleroUsers = lazy(() => import("./pages/AleroUsers"));
const AleroVendorDetail = lazy(() => import("./pages/AleroVendorDetail"));
const AleroVendors = lazy(() => import("./pages/AleroVendors"));
const SiaPolicies = lazy(() => import("./pages/SiaPolicies"));
const SiaPolicyDetail = lazy(() => import("./pages/SiaPolicyDetail"));
const SiaSessions = lazy(() => import("./pages/SiaSessions"));
const SiaSettings = lazy(() => import("./pages/SiaSettings"));
const SiaStrongAccounts = lazy(() => import("./pages/SiaStrongAccounts"));
const SiaStrongAccountDetail = lazy(() => import("./pages/SiaStrongAccountDetail"));
const SetupConnectorPools = lazy(() => import("./pages/SetupConnectorPools"));
const SetupConnectorDetail = lazy(() => import("./pages/SetupConnectorDetail"));
const SetupConnectors =lazy(() => import("./pages/SetupConnectors"));
const Approvals = lazy(() => import("./pages/Approvals"));
const Audit = lazy(() => import("./pages/Audit"));
const Campaigns = lazy(() => import("./pages/Campaigns"));
const Compliance = lazy(() => import("./pages/Compliance"));
const CyberArkStatus = lazy(() => import("./pages/CyberArkStatus"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Discovery = lazy(() =>
  import("./pages/Discovery").then((m) => ({ default: m.Discovery })),
);
const IdentityRoles = lazy(() => import("./pages/IdentityRoles"));
const KnowledgeBase = lazy(() => import("./pages/KnowledgeBase"));
const KnowledgeBaseArticle = lazy(() => import("./pages/KnowledgeBaseArticle"));
const Operators = lazy(() => import("./pages/Operators"));
const Platforms = lazy(() => import("./pages/Platforms"));
const Recordings = lazy(() => import("./pages/Recordings"));
const Reports = lazy(() => import("./pages/Reports"));
const Safes = lazy(() => import("./pages/Safes"));
const Search = lazy(() => import("./pages/Search"));
const Sessions = lazy(() => import("./pages/Sessions"));
const SsoUsers = lazy(() => import("./pages/SsoUsers"));
const SystemHealth = lazy(() => import("./pages/SystemHealth"));
const Tenants = lazy(() => import("./pages/Tenants"));
const Troubleshooting = lazy(() => import("./pages/Troubleshooting"));

function Protected({ children }: { children: ReactNode }) {
  const { operator, loading } = useAuth();
  const loc = useLocation();
  if (loading)
    return (
      <div className="flex h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  if (!operator) return <Navigate to="/login" state={{ from: loc }} replace />;
  if (operator.must_change_password && loc.pathname !== "/account")
    return <Navigate to="/account" replace />;
  return <>{children}</>;
}

function AdminOnly({ children }: { children: ReactNode }) {
  const { operator } = useAuth();
  if (operator?.role !== "platform_admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

function TenantAdminOnly({ children }: { children: ReactNode }) {
  const { operator } = useAuth();
  const q = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
    enabled: !!operator,
  });
  if (operator?.role === "platform_admin") return <>{children}</>;
  if (q.isLoading)
    return (
      <div className="flex h-40 items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  const allowed = (q.data ?? []).some((t) => roleAtLeast(t.my_role, "tenant_admin"));
  if (!allowed) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function Page({ children, admin }: { children: ReactNode; admin?: boolean }) {
  const inner = <Shell>{children}</Shell>;
  return <Protected>{admin ? <AdminOnly>{inner}</AdminOnly> : inner}</Protected>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Page><Accounts /></Page>} />
      <Route path="/safes" element={<Page><Safes /></Page>} />
      <Route path="/platforms" element={<Page><Platforms /></Page>} />
      <Route path="/sessions" element={<Page><Sessions /></Page>} />
      <Route path="/recordings" element={<Page><Recordings /></Page>} />
      <Route path="/system-health" element={<Page><SystemHealth /></Page>} />
      {/* la página Componentes se fusionó en System Health (2026-09-28) */}
      <Route path="/components" element={<Navigate to="/system-health" replace />} />
      <Route path="/cyberark-status" element={<Page><CyberArkStatus /></Page>} />
      <Route path="/search" element={<Page><Search /></Page>} />
      <Route path="/dashboard" element={<Page><Dashboard /></Page>} />
      <Route path="/discovery" element={<Page><Discovery /></Page>} />
      <Route path="/compliance" element={<Page><Compliance /></Page>} />
      <Route path="/sso-users" element={<Page><SsoUsers /></Page>} />
      <Route path="/identity-roles" element={<Page><IdentityRoles /></Page>} />
      <Route path="/alero-vendors" element={<Page><AleroVendors /></Page>} />
      <Route path="/alero-vendors/:vendorId" element={<Page><AleroVendorDetail /></Page>} />
      <Route path="/alero-users" element={<Page><AleroUsers /></Page>} />
      <Route path="/alero-users/:userId" element={<Page><AleroUserDetail /></Page>} />
      <Route path="/alero-roles" element={<Page><AleroRoles /></Page>} />
      <Route path="/alero-activities" element={<Page><AleroActivities /></Page>} />
      <Route path="/sia/policies" element={<Page><SiaPolicies /></Page>} />
      <Route path="/sia/policies/:policyId" element={<Page><SiaPolicyDetail /></Page>} />
      <Route path="/sia/sessions" element={<Page><SiaSessions /></Page>} />
      <Route path="/sia/strong-accounts" element={<Page><SiaStrongAccounts /></Page>} />
      <Route path="/sia/strong-accounts/:kind/:accountId" element={<Page><SiaStrongAccountDetail /></Page>} />
      <Route path="/sia/settings" element={<Page><SiaSettings /></Page>} />
      <Route path="/setup/connectors" element={<Page><SetupConnectors /></Page>} />
      <Route path="/setup/connectors/:connectorId" element={<Page><SetupConnectorDetail /></Page>} />
      <Route path="/setup/connector-pools" element={<Page><SetupConnectorPools /></Page>} />
      <Route path="/troubleshooting" element={<Page><Troubleshooting /></Page>} />
      <Route path="/campaigns" element={<Page><Campaigns /></Page>} />
      <Route path="/approvals" element={<Page><Approvals /></Page>} />
      <Route path="/knowledge-base" element={<Page><KnowledgeBase /></Page>} />
      <Route path="/knowledge-base/:id" element={<Page><KnowledgeBaseArticle /></Page>} />
      <Route path="/reports" element={<Page><Reports /></Page>} />
      <Route
        path="/backups"
        element={
          <Protected>
            <TenantAdminOnly>
              <Shell>
                <AccountBackups />
              </Shell>
            </TenantAdminOnly>
          </Protected>
        }
      />
      <Route
        path="/audit"
        element={
          <Protected>
            <TenantAdminOnly>
              <Shell>
                <Audit />
              </Shell>
            </TenantAdminOnly>
          </Protected>
        }
      />
      <Route path="/account" element={<Page><Account /></Page>} />
      <Route path="/tenants" element={<Page admin><Tenants /></Page>} />
      <Route path="/operators" element={<Page admin><Operators /></Page>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
