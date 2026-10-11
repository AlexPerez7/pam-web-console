import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BookOpen,
  ExternalLink,
  Loader2,
  Plus,
  RefreshCcw,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { AccountActivityFeed } from "@/components/ActivityFeed";
import { Card, Chip, StatusPill } from "@/components/AccountDetailPanel";
import { DryRunDialog } from "@/components/DryRunDialog";
import { KbArticleFormDialog } from "@/components/KbArticleFormDialog";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { classify } from "@/lib/activityClassify";
import { api } from "@/lib/api";
import { CPM_LABEL, isInProcess, type CpmAction } from "@/lib/cpmActions";
import { cpmFailures } from "@/lib/cpmFailures";
import { fmtDateTime, relTime } from "@/lib/format";
import { invalidateAccount } from "@/lib/invalidate";
import { matchKbArticle } from "@/lib/kbMatch";
import { roleAtLeast } from "@/lib/roles";
import { cn } from "@/lib/utils";
import type {
  Account,
  AccountActivitiesResponse,
  AccountDetailResponse,
  KbArticlesResponse,
  TenantSummary,
} from "@/lib/types";

/** Detalle **específico para Troubleshooting** — a diferencia de
 *  `AccountDetailPanel` (gestión general de la cuenta), lleva al frente
 *  "¿qué pasó y qué hago ahora": el motivo de la última falla del CPM +
 *  el historial de fallas, con sólo las acciones que sirven para
 *  resolverla (Verificar/Cambiar/Reconciliar). Botón "Ver cuenta
 *  completa" abre el panel genérico para lo demás (editar, revelar,
 *  vincular, borrar). Ver `docs/specs/troubleshooting-errores-cpm.md`. */
export function TroubleshootingAccountPanel({
  tenant,
  accountId,
  opened,
  onClose,
  onOpenFull,
}: {
  tenant: string;
  accountId?: string;
  opened: boolean;
  onClose: () => void;
  onOpenFull: () => void;
}) {
  const qc = useQueryClient();
  const [cpmAction, setCpmAction] = useState<CpmAction | null>(null);
  const [addingKb, setAddingKb] = useState(false);
  const acc = encodeURIComponent(accountId ?? "");

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
    enabled: opened,
  });

  const detail = useQuery({
    queryKey: ["account-detail", tenant, accountId],
    queryFn: () => api<AccountDetailResponse>(`/tenants/${tenant}/accounts/${acc}`),
    enabled: opened && !!accountId,
    refetchInterval: (q) =>
      isInProcess(q.state.data?.account.secretManagement?.status) ? 5000 : false,
  });

  const account: Account | undefined = detail.data?.account;
  const sm = account?.secretManagement;
  const cpmBusy = isInProcess(sm?.status);

  const activities = useQuery({
    queryKey: ["account-activities", tenant, accountId],
    queryFn: () =>
      api<AccountActivitiesResponse>(`/tenants/${tenant}/accounts/${acc}/activities`),
    enabled: opened && !!accountId,
    refetchInterval: cpmBusy ? 5000 : false,
  });

  const kbArticles = useQuery({
    queryKey: ["kb-articles"],
    queryFn: () => api<KbArticlesResponse>("/knowledge-base"),
    enabled: opened,
  });

  if (!accountId) {
    return <Sheet open={opened} onOpenChange={(o) => !o && onClose()} />;
  }

  const role = t.data?.my_role;
  const canOperator = roleAtLeast(role, "operator");
  const canChange = canOperator;
  const canVerify = canOperator;

  const name = String(account?.name ?? accountId);
  const heading =
    account?.userName && account?.address ? `${account.userName}@${account.address}` : name;

  const failures = cpmFailures(activities.data?.activities ?? []);
  const lastFail = failures[0];
  const lastFailLabel = lastFail ? classify(lastFail.Action).label : null;
  const kbMatch = matchKbArticle(lastFail?.Reason, kbArticles.data?.articles ?? []);

  function refresh() {
    detail.refetch();
    activities.refetch();
  }

  return (
    <>
      <Sheet open={opened} onOpenChange={(o) => !o && onClose()}>
        <SheetContent className="flex w-full max-w-xl flex-col gap-0 p-0">
          <SheetTitle className="sr-only">{heading} — Troubleshooting</SheetTitle>

          <div className="sticky top-0 z-10 flex flex-col gap-3 bg-card px-6 pb-0 pt-6">
            <div className="flex items-center gap-1.5 pr-8 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <AlertTriangle className="size-3.5 shrink-0" />
              Troubleshooting · {(t.data?.name ?? tenant).toString().toUpperCase()}
            </div>

            <h1 className="pr-8 text-[21px] font-bold leading-tight text-balance break-words text-navy-900">
              {heading}
            </h1>

            <div className="flex flex-wrap items-center gap-2">
              {account?.platformId && <Chip tone="platform">{String(account.platformId)}</Chip>}
              {account?.safeName && <Chip tone="safe">{String(account.safeName)}</Chip>}
              {sm && <StatusPill status={sm.status} />}
            </div>

            <div className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
              {canVerify && (
                <Button variant="outline" size="sm" onClick={() => setCpmAction("verify")}>
                  <ShieldCheck className="size-3.5" />
                  Verificar
                </Button>
              )}
              {canChange && (
                <Button size="sm" onClick={() => setCpmAction("change")}>
                  <RefreshCw className="size-3.5" />
                  Cambiar ahora
                </Button>
              )}
              {canChange && (
                <Button variant="outline" size="sm" onClick={() => setCpmAction("reconcile")}>
                  <RefreshCcw className="size-3.5" />
                  Reconciliar
                </Button>
              )}
              <Button variant="ghost" size="sm" className="ml-auto" onClick={onOpenFull}>
                Ver cuenta completa →
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-4 px-6 py-5">
            {detail.isError && (
              <Alert variant="destructive">{(detail.error as Error).message}</Alert>
            )}
            {!account && detail.isLoading && (
              <div className="flex justify-center py-10">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            )}

            {account && (
              <>
                {lastFail ? (
                  <Alert variant="destructive">
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold">Último error: {lastFailLabel}</span>
                        <span className="shrink-0 text-xs opacity-80" title={fmtDateTime(lastFail.Date)}>
                          {relTime(lastFail.Date)}
                        </span>
                      </div>
                      {lastFail.Reason && <p className="italic">“{lastFail.Reason}”</p>}
                    </div>
                  </Alert>
                ) : (
                  activities.data && (
                    <Alert variant="warning">
                      Sin fallas registradas en la actividad reciente — puede estar en gestión
                      manual por otro motivo (ver "Ver cuenta completa" → Detalles).
                    </Alert>
                  )
                )}

                {lastFail &&
                  (kbMatch ? (
                    <Card
                      icon={<BookOpen className="size-3.5 text-sky-600" />}
                      title="Solución conocida"
                    >
                      <div className="flex flex-col gap-2 p-4 text-sm">
                        <p className="font-semibold text-navy-900">{kbMatch.title}</p>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Causa posible
                          </p>
                          <p className="whitespace-pre-wrap">{kbMatch.cause}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Solución
                          </p>
                          <p className="whitespace-pre-wrap">{kbMatch.solution}</p>
                        </div>
                        {kbMatch.source_url && (
                          <a
                            href={kbMatch.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-xs font-semibold text-sky-600 hover:underline"
                          >
                            <ExternalLink className="size-3" />
                            Ver artículo
                          </a>
                        )}
                      </div>
                    </Card>
                  ) : (
                    kbArticles.data && (
                      <Button variant="outline" size="sm" onClick={() => setAddingKb(true)}>
                        <Plus className="size-3.5" />
                        Agregar a la base de conocimiento
                      </Button>
                    )
                  ))}

                <Card
                  icon={<RefreshCcw className="size-3.5 text-sky-600" />}
                  title={`Historial de fallas${failures.length ? ` · ${failures.length}` : ""}`}
                  headerRight={
                    <button
                      onClick={refresh}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-sky-600 hover:underline"
                    >
                      <RefreshCw
                        className={cn(
                          "size-3",
                          (activities.isFetching || cpmBusy) && "animate-spin",
                        )}
                      />
                      {cpmBusy ? "CPM en curso…" : "Actualizar"}
                    </button>
                  }
                >
                  <div className="p-4">
                    {activities.isLoading && (
                      <Loader2 className="size-4 animate-spin text-muted-foreground" />
                    )}
                    {activities.isError && (
                      <Alert variant="destructive">{(activities.error as Error).message}</Alert>
                    )}
                    {activities.data &&
                      (failures.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          Sin fallas en la actividad reciente.
                        </p>
                      ) : (
                        <AccountActivityFeed activities={failures} />
                      ))}
                  </div>
                </Card>
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <DryRunDialog
        open={cpmAction !== null}
        onClose={() => setCpmAction(null)}
        path={`/tenants/${tenant}/accounts/${acc}/${cpmAction ?? "verify"}`}
        changes={{}}
        title={cpmAction ? CPM_LABEL[cpmAction] : ""}
        onDone={() => {
          setCpmAction(null);
          invalidateAccount(qc, tenant, accountId);
          qc.invalidateQueries({ queryKey: ["troubleshooting", tenant] });
        }}
      />

      <KbArticleFormDialog
        open={addingKb}
        onClose={() => setAddingKb(false)}
        initialPattern={lastFail?.Reason}
        onDone={() => setAddingKb(false)}
      />
    </>
  );
}
