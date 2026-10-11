import { useQuery } from "@tanstack/react-query";
import { IdentityUserLockSection } from "@/components/IdentityUserLockSection";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { IdentityUserDetailResponse } from "@/lib/types";

type Obj = Record<string, unknown>;

function primitive(key: string, v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "boolean") return v ? "sí" : "no";
  if (typeof v === "number" && /(time|date)$/i.test(key)) return fmtDateTime(v);
  return String(v);
}

/** "10.1.2.1;10.1.2.2" -> ["10.1.2.1", "10.1.2.2"], sin vacíos. */
function splitList(v: string): string[] {
  return v
    .split(";")
    .map((x) => x.trim())
    .filter(Boolean);
}

function KV({ data }: { data: Obj }) {
  const entries = Object.entries(data);
  const prims = entries.filter(
    ([k, v]) => (v == null || typeof v !== "object") && k !== "remoteMachines",
  );
  const objs = entries.filter(([, v]) => v != null && typeof v === "object");
  const remoteMachines = entries.find(([k]) => k === "remoteMachines")?.[1];
  return (
    <div className="flex flex-col gap-2.5">
      {prims.length > 0 && (
        <div className="grid grid-cols-[190px_1fr] gap-x-3 gap-y-1 text-xs">
          {prims.map(([k, v]) => (
            <div key={k} className="contents">
              <span className="text-muted-foreground">{k}</span>
              <span className="break-words">{primitive(k, v)}</span>
            </div>
          ))}
        </div>
      )}
      {typeof remoteMachines === "string" && remoteMachines && (
        <div className="text-xs">
          <span className="text-muted-foreground">remoteMachines</span>
          <div className="mt-1 flex flex-col gap-0.5">
            {splitList(remoteMachines).map((m) => (
              <span key={m} className="break-words">
                {m}
              </span>
            ))}
          </div>
        </div>
      )}
      {objs.map(([k, v]) => (
        <div key={k}>
          <p className="mb-1 text-xs font-semibold">{k}</p>
          {Array.isArray(v) ? (
            v.length === 0 ? (
              <span className="text-xs text-muted-foreground">—</span>
            ) : (
              <pre className="overflow-x-auto rounded-md bg-muted p-2 text-[11px]">
                {JSON.stringify(v, null, 2)}
              </pre>
            )
          ) : (
            <KV data={v as Obj} />
          )}
        </div>
      ))}
    </div>
  );
}

interface Props {
  opened: boolean;
  onClose: () => void;
  title: string;
  object: Obj | null;
  tenant?: string;
  identityUserId?: string;
}

export function DetailDrawer({
  opened,
  onClose,
  title,
  object,
  tenant,
  identityUserId,
}: Props) {
  const isIdentityUser = !!tenant && !!identityUserId;

  const identityUserDetail = useQuery({
    queryKey: ["identity-user-detail", tenant, identityUserId],
    queryFn: () =>
      api<IdentityUserDetailResponse>(
        `/tenants/${tenant}/identity-users/${encodeURIComponent(identityUserId ?? "")}`,
      ),
    enabled: opened && isIdentityUser,
  });

  const freshIdentityUser =
    isIdentityUser && identityUserDetail.data ? identityUserDetail.data.user : undefined;
  const props = freshIdentityUser ? (freshIdentityUser as Obj) : object;
  const identityLocked = isIdentityUser
    ? Boolean((freshIdentityUser ?? object ?? {})?.Locked)
    : null;

  return (
    <Sheet open={opened} onOpenChange={(o) => !o && onClose()}>
      <SheetContent>
        <SheetTitle>{title}</SheetTitle>

        {isIdentityUser && identityLocked !== null && (
          <div className="mb-1">
            <Badge variant={identityLocked ? "danger" : "success"}>
              {identityLocked ? "bloqueado" : "activo"}
            </Badge>
          </div>
        )}

        <section>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Propiedades
          </p>
          {props ? (
            <KV data={props} />
          ) : (
            <p className="text-sm text-muted-foreground">Sin datos.</p>
          )}
        </section>

        {isIdentityUser && (
          <IdentityUserLockSection
            tenant={tenant!}
            userId={identityUserId!}
            locked={identityLocked ?? undefined}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}
