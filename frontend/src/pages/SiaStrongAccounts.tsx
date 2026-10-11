import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { SiaWarnings } from "@/components/SiaWarnings";
import { TenantScope } from "@/components/TenantScope";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { dash, fmtDate } from "@/lib/format";
import {
  SECRET_TYPE_LABEL,
  TARGET_SET_TYPE_LABEL,
  strongAccountsQuery,
  type SiaDbStrongAccount,
  type SiaVmStrongAccount,
} from "@/lib/sia";

function ActiveBadge({ active }: { active: boolean }) {
  return active ? (
    <Badge variant="success">Habilitada</Badge>
  ) : (
    <Badge variant="neutral">Deshabilitada</Badge>
  );
}

const vmCols: Column<SiaVmStrongAccount>[] = [
  { key: "name", header: "Nombre", render: (r) => dash(r.name), sortValue: (r) => r.name },
  {
    key: "domain",
    header: "Dominio",
    width: 160,
    render: (r) => (r.accountType === "local" ? "Cuenta local" : r.domain),
    sortValue: (r) => r.domain,
  },
  {
    key: "targetSets",
    header: "Target sets",
    width: 220,
    render: (r) =>
      r.targetSets.length ? r.targetSets.map((t) => t.name).join(", ") : <span className="text-muted-foreground">Ninguno</span>,
    sortValue: (r) => r.targetSets.length,
  },
  {
    key: "ephemeral",
    header: "Usuarios efímeros de dominio",
    width: 200,
    render: (r) => (r.accountType === "local" ? "—" : r.ephemeralDomainUsers ? "Sí" : "No"),
    sortValue: (r) => (r.ephemeralDomainUsers ? 1 : 0),
  },
  {
    key: "safe",
    header: "Safe",
    width: 200,
    render: (r) => dash(r.linkedSafe),
    sortValue: (r) => r.linkedSafe,
  },
  {
    key: "active",
    header: "Estado",
    width: 120,
    render: (r) => <ActiveBadge active={r.active} />,
    sortValue: (r) => (r.active ? 1 : 0),
  },
  {
    key: "type",
    header: "Tipo",
    width: 190,
    defaultHidden: true,
    render: (r) => (r.type ? (SECRET_TYPE_LABEL[r.type] ?? r.type) : "—"),
  },
];

const dbCols: Column<SiaDbStrongAccount>[] = [
  { key: "name", header: "Nombre", render: (r) => dash(r.name), sortValue: (r) => r.name },
  {
    key: "type",
    header: "Tipo",
    width: 190,
    render: (r) => (r.type ? (SECRET_TYPE_LABEL[r.type] ?? r.type) : "—"),
    sortValue: (r) => r.type,
  },
  {
    key: "username",
    header: "Usuario",
    width: 160,
    render: (r) => dash(r.username ?? r.account),
    sortValue: (r) => r.username,
  },
  {
    key: "databases",
    header: "Bases de datos",
    width: 240,
    render: (r) =>
      r.databases.length ? r.databases.map((d) => d.name).join(", ") : <span className="text-muted-foreground">Ninguna</span>,
    sortValue: (r) => r.databases.length,
  },
  {
    key: "linked",
    header: "Cuenta vinculada",
    width: 220,
    render: (r) =>
      dash(r.linkedSafe && r.linkedAccount ? `${r.linkedSafe} / ${r.linkedAccount}` : r.linkedAccount),
  },
  {
    key: "active",
    header: "Estado",
    width: 120,
    render: (r) => <ActiveBadge active={r.active} />,
    sortValue: (r) => (r.active ? 1 : 0),
  },
  {
    key: "updatedAt",
    header: "Modificada",
    width: 110,
    defaultHidden: true,
    render: (r) => fmtDate(r.updatedAt ?? r.createdAt),
  },
];

function StrongAccountsView({ tenant }: { tenant: string }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "db" ? "db" : "vm";
  const q = useQuery(strongAccountsQuery(tenant));
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  const d = q.data;
  return (
    <>
      <p className="mb-3 text-sm text-muted-foreground">
        Cuentas privilegiadas que SIA usa para crear los usuarios efímeros del acceso ZSP a los targets.
        Sólo metadatos: PAM Web Console nunca pide ni muestra su contraseña.
      </p>
      <SiaWarnings warnings={d.warnings} />
      <Tabs
        value={tab}
        onValueChange={(v) =>
          setParams(
            (p) => {
              p.set("tab", v);
              return p;
            },
            { replace: true },
          )
        }
      >
        <TabsList>
          <TabsTrigger value="vm">Máquinas virtuales ({d.vm.length})</TabsTrigger>
          <TabsTrigger value="db">Bases de datos ({d.db.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="vm" className="flex flex-col gap-3">
          {d.targetSetsWithoutAccount.length > 0 && (
            <Alert variant="warning">
              Target sets que apuntan a una strong account que no aparece en la lista:{" "}
              {d.targetSetsWithoutAccount
                .map((t) => `${t.name} (${t.type ? (TARGET_SET_TYPE_LABEL[t.type] ?? t.type) : "—"})`)
                .join(", ")}
              .
            </Alert>
          )}
          <DataTable
            rows={d.vm}
            columns={vmCols}
            tableId="sia-strong-accounts-vm"
            getRowKey={(r) => r.id}
            filterOn={(r) => [r.name, r.domain, r.linkedSafe, r.targetSets.map((t) => t.name).join(" ")]}
            filterPlaceholder="Buscar por nombre, dominio, safe o target set"
            countNoun="strong accounts"
            minWidth={1000}
            onRowClick={(r) => navigate(`/sia/strong-accounts/vm/${encodeURIComponent(r.id)}?tenant=${tenant}`)}
          />
        </TabsContent>
        <TabsContent value="db" className="flex flex-col gap-3">
          <DataTable
            rows={d.db}
            columns={dbCols}
            tableId="sia-strong-accounts-db"
            getRowKey={(r) => r.id}
            filterOn={(r) => [r.name, r.username, r.linkedAccount, r.databases.map((x) => x.name).join(" ")]}
            filterPlaceholder="Buscar por nombre, usuario o base de datos"
            countNoun="strong accounts"
            minWidth={1000}
            onRowClick={(r) => navigate(`/sia/strong-accounts/db/${encodeURIComponent(r.id)}?tenant=${tenant}`)}
          />
          {d.databasesWithoutAccount.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Bases de datos registradas sin strong account:{" "}
              {d.databasesWithoutAccount.map((x) => x.name).join(", ")}.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </>
  );
}

export default function SiaStrongAccounts() {
  return (
    <TenantScope title="Strong accounts">{(tenant) => <StrongAccountsView tenant={tenant} />}</TenantScope>
  );
}
