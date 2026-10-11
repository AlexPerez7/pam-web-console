import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import { invalidateAccount } from "@/lib/invalidate";
import type { Account, AccountsResponse } from "@/lib/types";

type Obj = Record<string, unknown>;
type LinkType = "logon" | "reconcile";

/** Diálogo "Vincular cuenta" — versión controlada de `AccountLinkSection`,
 *  misma lógica de búsqueda/entrada manual, ahora en un `Dialog` en vez de
 *  sección inline. */
export function AccountLinkDialog({
  open,
  onClose,
  tenant,
  accountId,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  tenant: string;
  accountId: string;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [linkType, setLinkType] = useState<LinkType>("logon");
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [manual, setManual] = useState(false);
  const [picked, setPicked] = useState<Account | null>(null);
  const [mSafe, setMSafe] = useState("");
  const [mName, setMName] = useState("");
  const [mFolder, setMFolder] = useState("Root");
  const [dialog, setDialog] = useState<Obj | null>(null);

  useEffect(() => {
    if (open) {
      setLinkType("logon");
      setTerm("");
      setDebounced("");
      setManual(false);
      setPicked(null);
      setMSafe("");
      setMName("");
      setMFolder("Root");
    }
  }, [open]);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(term.trim()), 300);
    return () => clearTimeout(id);
  }, [term]);

  const search = useQuery({
    queryKey: ["account-search", tenant, debounced],
    queryFn: () =>
      api<AccountsResponse>(
        `/tenants/${tenant}/accounts?search=${encodeURIComponent(debounced)}&limit=10`,
      ),
    enabled: open && !manual && debounced.length >= 2,
  });

  const linkedSafe = manual ? mSafe.trim() : (picked?.safeName ?? "");
  const linkedName = manual ? mName.trim() : (picked?.name ?? "");
  const linkedFolder = manual ? mFolder.trim() || "Root" : "Root";

  function preview() {
    setDialog({
      link_type: linkType,
      linked_safe: linkedSafe,
      linked_name: linkedName,
      linked_folder: linkedFolder,
    });
  }

  return (
    <>
      <Dialog open={open && dialog === null} onOpenChange={(o) => !o && onClose()}>
        <DialogContent>
          <DialogTitle>Vincular cuenta</DialogTitle>
          <div className="flex flex-col gap-3">
            <div className="flex gap-1.5">
              {(["logon", "reconcile"] as LinkType[]).map((lt) => (
                <button
                  key={lt}
                  onClick={() => setLinkType(lt)}
                  className={
                    "rounded-full border px-3 py-0.5 text-xs " +
                    (linkType === lt
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border hover:bg-muted")
                  }
                >
                  {lt === "logon" ? "Logon" : "Reconciliación"}
                </button>
              ))}
            </div>

            {picked && !manual ? (
              <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-sm">
                <span className="min-w-0 truncate">
                  <span className="font-medium">{picked.name}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    · {dash(picked.userName)} · {dash(picked.address)} · safe{" "}
                    {dash(picked.safeName)}
                  </span>
                </span>
                <button
                  onClick={() => setPicked(null)}
                  className="shrink-0 text-xs text-sky-600 hover:underline"
                >
                  cambiar
                </button>
              </div>
            ) : manual ? (
              <div className="flex flex-col gap-2 text-xs text-muted-foreground">
                <label>
                  Safe de la cuenta a vincular
                  <Input
                    className="mt-1 h-8 text-sm"
                    value={mSafe}
                    onChange={(e) => setMSafe(e.target.value)}
                  />
                </label>
                <div className="flex gap-2">
                  <label className="flex-1">
                    Nombre de la cuenta
                    <Input
                      className="mt-1 h-8 text-sm"
                      value={mName}
                      onChange={(e) => setMName(e.target.value)}
                    />
                  </label>
                  <label className="flex-1">
                    Folder
                    <Input
                      className="mt-1 h-8 text-sm"
                      value={mFolder}
                      onChange={(e) => setMFolder(e.target.value)}
                    />
                  </label>
                </div>
                <button onClick={() => setManual(false)} className="self-start text-sky-600 hover:underline">
                  ← usar el buscador
                </button>
              </div>
            ) : (
              <div className="flex flex-col gap-1.5">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    autoFocus
                    className="h-9 pl-8 text-sm"
                    placeholder="Buscar la cuenta a vincular…"
                    value={term}
                    onChange={(e) => setTerm(e.target.value)}
                  />
                </div>
                {!!search.data?.accounts.length && (
                  <ul className="max-h-48 divide-y divide-border overflow-y-auto rounded-md border border-border bg-card">
                    {search.data.accounts
                      .filter((a) => a.id !== accountId)
                      .map((a) => (
                        <li key={a.id}>
                          <button
                            onClick={() => setPicked(a)}
                            className="flex w-full flex-col items-start px-2.5 py-1.5 text-left text-sm hover:bg-muted"
                          >
                            <span className="font-medium">{a.name}</span>
                            <span className="text-[11px] text-muted-foreground">
                              {dash(a.userName)} · {dash(a.address)} · safe {dash(a.safeName)}
                            </span>
                          </button>
                        </li>
                      ))}
                  </ul>
                )}
                {search.data && search.data.accounts.length === 0 && debounced.length >= 2 && (
                  <span className="text-xs text-muted-foreground">Sin resultados.</span>
                )}
                <button
                  onClick={() => setManual(true)}
                  className="self-start text-xs text-sky-600 hover:underline"
                >
                  …o ingresar safe / nombre a mano
                </button>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={onClose}>
              Cancelar
            </Button>
            <Button onClick={preview} disabled={!linkedSafe || !linkedName}>
              Previsualizar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/accounts/${encodeURIComponent(accountId)}/link`}
        changes={dialog ?? {}}
        title="Vincular cuenta"
        onDone={() => {
          setDialog(null);
          onClose();
          invalidateAccount(qc, tenant, accountId);
          onDone();
        }}
      />
    </>
  );
}
