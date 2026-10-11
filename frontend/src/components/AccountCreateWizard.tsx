import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AppWindow,
  Boxes,
  Cloud,
  Database,
  Globe,
  HardDrive,
  KeyRound,
  Layers,
  Loader2,
  type LucideIcon,
  Monitor,
  Plus,
  Router,
  Search,
  ShieldCheck,
  Terminal,
  Wifi,
  X,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { PlatformsResponse, SafeNamesResponse, TenantSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

type PropRow = { id: string; key: string; value: string };

/** Sugerencias de nombres de propiedad por tipo de sistema — sólo atajos de
 *  UI (un clic agrega la fila), NUNCA vienen de CyberArk: "Get Platform
 *  Details" no expone el esquema de campos del Add Account de PVWA (esa
 *  config vive en Policies.xml, sin API pública — verificado 2026-09-11
 *  contra Globex). El operador puede agregar/editar cualquier propiedad a
 *  mano igual. */
function suggestedProperties(systemType: string): string[] {
  const t = systemType.toLowerCase();
  if (t.includes("windows")) return ["Log On To", "User DN", "Port", "Comments"];
  if (t.includes("nix") || t.includes("linux") || t.includes("unix"))
    return ["Use Sudo On Reconcile", "Port", "Comments"];
  if (t.includes("database")) return ["DSN", "Port", "Database", "Comments"];
  if (t.includes("network")) return ["Port", "Comments"];
  return ["Comments"];
}

type Obj = Record<string, unknown>;
type Step = 1 | 2 | 3 | 4;

const INITIAL: Step = 1;

/** Ícono representativo por tipo de sistema, estilo PVWA. Match por
 *  substring (case-insensitive) sobre el `systemType` real de la
 *  plataforma en CyberArk — no es un enum cerrado, cada tenant puede tener
 *  el suyo, por eso el fallback genérico (`Layers`). */
function systemTypeIcon(systemType: string): LucideIcon {
  const t = systemType.toLowerCase();
  if (t.includes("windows")) return Monitor;
  if (t.includes("nix") || t.includes("linux") || t.includes("unix")) return Terminal;
  if (t.includes("database") || t === "db") return Database;
  if (t.includes("network")) return Router;
  if (t.includes("security") || t.includes("appliance")) return ShieldCheck;
  if (t.includes("directory")) return KeyRound;
  if (t.includes("cloud")) return Cloud;
  if (t.includes("remote")) return Wifi;
  if (t.includes("website") || t.includes("web")) return Globe;
  if (t.includes("application")) return AppWindow;
  if (t.includes("operating system")) return HardDrive;
  if (t.includes("imported")) return Boxes;
  return Layers;
}

const STEP_LABELS: Record<Step, string> = {
  1: "Tipo de sistema",
  2: "Plataforma",
  3: "Safe",
  4: "Propiedades",
};

/** Stepper vertical del panel, calcado del "Add Account" real del PVWA
 *  (círculos numerados + línea conectora, subtítulo con la selección de
 *  cada paso ya hecho). Sólo los pasos YA completados (`s < step`) son
 *  clickeables — saltar hacia adelante no tiene sentido, todavía no hay
 *  datos para ese paso. */
function StepNav({
  step,
  systemType,
  platformId,
  safeName,
  onJump,
}: {
  step: Step;
  systemType: string;
  platformId: string;
  safeName: string;
  onJump: (s: Step) => void;
}) {
  const subtitle: Record<Step, string> = {
    1: systemType,
    2: platformId,
    3: safeName,
    4: "",
  };
  return (
    <nav className="flex w-52 shrink-0 flex-col border-r border-border py-6 pl-6 pr-4">
      {([1, 2, 3, 4] as Step[]).map((s) => {
        const done = s < step;
        const current = s === step;
        return (
          <div key={s} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                  current
                    ? "border-primary bg-primary text-primary-foreground"
                    : done
                      ? "border-primary text-primary"
                      : "border-border text-muted-foreground",
                )}
              >
                {s}
              </span>
              {s < 4 && <span className="w-px flex-1 bg-border" />}
            </div>
            <button
              type="button"
              disabled={!done}
              onClick={() => done && onJump(s)}
              className={cn("flex-1 pb-6 text-left", done && "cursor-pointer")}
            >
              <div
                className={cn(
                  "text-sm font-medium",
                  current
                    ? "text-navy-900"
                    : done
                      ? "text-foreground"
                      : "text-muted-foreground",
                )}
              >
                {STEP_LABELS[s]}
              </div>
              {subtitle[s] && <div className="text-xs text-muted-foreground">{subtitle[s]}</div>}
            </button>
          </div>
        );
      })}
    </nav>
  );
}

/** Sin `open`, el componente maneja su propio estado y muestra su botón.
 *  Con `open`/`onOpenChange` lo controla el padre (ej. el botón "Crear
 *  cuenta" con desplegable de la grilla de Cuentas) y `hideTrigger` oculta
 *  el botón propio. */
interface DialogControl {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  hideTrigger?: boolean;
}

/** Botón + wizard "Crear cuenta" del toolbar de Cuentas, calcado del Add
 *  Account de PVWA: tipo de sistema -> plataforma -> safe -> campos (los
 *  específicos de la plataforma se leen en vivo de CyberArk). Panel
 *  completo a la derecha (Sheet, no un modal chico centrado) con stepper
 *  vertical clickeable — mismo layout que el Add Account real, a pedido de
 *  Alex (2026-09-16): el menú lateral de PAM Web Console queda visible a la
 *  izquierda, igual que en PVWA. Sólo aparece si el tenant tiene
 *  `account.create` habilitada y el operador es `operator+`. Ver
 *  `docs/specs/plantillas-alta-cuenta-safe.md`. */
export function AccountCreateWizard({
  tenant,
  ...props
}: { tenant: string } & DialogControl) {
  const qc = useQueryClient();
  const [openState, setOpenState] = useState(false);
  const open = props.open ?? openState;
  const setOpen = (v: boolean) => {
    if (props.open === undefined) setOpenState(v);
    props.onOpenChange?.(v);
  };
  const [step, setStep] = useState<Step>(INITIAL);
  const [systemType, setSystemType] = useState("");
  const [platformId, setPlatformId] = useState("");
  const [platformSearch, setPlatformSearch] = useState("");
  const [safeName, setSafeName] = useState("");
  const [safeSearch, setSafeSearch] = useState("");
  const [address, setAddress] = useState("");
  const [userName, setUserName] = useState("");
  const [customizeName, setCustomizeName] = useState(false);
  const [name, setName] = useState("");
  const [secret, setSecret] = useState("");
  const [confirmSecret, setConfirmSecret] = useState("");
  const [autoManagement, setAutoManagement] = useState(true);
  const [manualReason, setManualReason] = useState("");
  const [remoteMachines, setRemoteMachines] = useState("");
  const [restrictRemote, setRestrictRemote] = useState(false);
  const [customProps, setCustomProps] = useState<PropRow[]>([]);
  const nextRowId = useRef(0);
  const [dialog, setDialog] = useState<Obj | null>(null);

  function addPropertyRow(key = "") {
    const id = String(nextRowId.current++);
    setCustomProps((rows) => [...rows, { id, key, value: "" }]);
  }
  function updatePropertyRow(id: string, patch: Partial<PropRow>) {
    setCustomProps((rows) => rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }
  function removePropertyRow(id: string) {
    setCustomProps((rows) => rows.filter((r) => r.id !== id));
  }
  /** Al entrar al paso 4 por primera vez, precarga las sugerencias como
   *  filas ya visibles (como PVWA) en vez de esperar un clic — el operador
   *  las borra si no aplican para esa plataforma puntual. */
  function seedSuggestedProps() {
    if (customProps.length > 0) return;
    setCustomProps(
      suggestedProperties(systemType).map((key) => ({
        id: String(nextRowId.current++),
        key,
        value: "",
      })),
    );
  }

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const allowed = roleAtLeast(t.data?.my_role, "operator");
  // Precarga plataformas/safes ni bien se sabe que el botón va a aparecer
  // (no recién al abrir el modal) -- `/platforms` es un payload pesado
  // contra CyberArk en un cache-miss (`platforms_rows_cached`, TTL 300s);
  // esperar al click hacía sentir "Crear cuenta" lento la primera vez.
  const platforms = useQuery({
    queryKey: ["inv", tenant, "platforms"],
    queryFn: () => api<PlatformsResponse>(`/tenants/${tenant}/platforms`),
    enabled: open || allowed,
    staleTime: 5 * 60_000,
  });
  const safes = useQuery({
    queryKey: ["safe-names", tenant],
    queryFn: () => api<SafeNamesResponse>(`/tenants/${tenant}/safe-names`),
    enabled: open || allowed,
    staleTime: 5 * 60_000,
  });
  const active = useMemo(
    () => (platforms.data?.platforms ?? []).filter((p) => p.general?.active !== false),
    [platforms.data],
  );
  const systemTypes = useMemo(
    () => Array.from(new Set(active.map((p) => p.general?.systemType).filter(Boolean))) as string[],
    [active],
  );
  const platformsOfType = useMemo(
    () => active.filter((p) => p.general?.systemType === systemType),
    [active, systemType],
  );
  const filteredPlatforms = useMemo(() => {
    const term = platformSearch.trim().toLowerCase();
    if (!term) return platformsOfType;
    return platformsOfType.filter((p) =>
      [p.general?.name, p.general?.id].some((v) => v?.toLowerCase().includes(term)),
    );
  }, [platformsOfType, platformSearch]);
  const filteredSafes = useMemo(() => {
    const term = safeSearch.trim().toLowerCase();
    const names = safes.data?.names ?? [];
    if (!term) return names;
    return names.filter((n) => n.toLowerCase().includes(term));
  }, [safes.data, safeSearch]);

  if (!allowed) return null;

  function reset() {
    setOpen(false);
    setStep(INITIAL);
    setSystemType("");
    setPlatformId("");
    setPlatformSearch("");
    setSafeName("");
    setSafeSearch("");
    setAddress("");
    setUserName("");
    setCustomizeName(false);
    setName("");
    setSecret("");
    setConfirmSecret("");
    setAutoManagement(true);
    setManualReason("");
    setRemoteMachines("");
    setRestrictRemote(false);
    setCustomProps([]);
  }

  const secretsOk = secret === confirmSecret;
  const reasonOk = autoManagement || !!manualReason.trim();
  const canPreview = !!address.trim() && !!userName.trim() && secretsOk && reasonOk;

  function preview() {
    const properties = Object.fromEntries(
      customProps
        .filter((r) => r.key.trim() && r.value.trim())
        .map((r) => [r.key.trim(), r.value.trim()]),
    );
    const changes: Obj = {
      safe_name: safeName,
      platform_id: platformId,
      user_name: userName.trim(),
      address: address.trim(),
      properties,
      automatic_management_enabled: autoManagement,
    };
    if (customizeName && name.trim()) changes.name = name.trim();
    if (secret) changes.secret = secret;
    if (!autoManagement) changes.manual_management_reason = manualReason.trim();
    if (remoteMachines.trim()) {
      const rm = remoteMachines
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
        .join(";");
      changes.remote_machines = rm;
      changes.access_restricted_to_remote_machines = restrictRemote;
    }
    setDialog(changes);
  }

  return (
    <>
      {!props.hideTrigger && (
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-3.5" />
          Crear cuenta
        </Button>
      )}

      <Sheet open={open} onOpenChange={(o) => !o && reset()}>
        <SheetContent
          className="flex w-full flex-col gap-0 p-0"
          // Llega hasta el borde del sidebar navy (`w-56` = 14rem en
          // App.tsx), no un ancho fijo chico -- pedido de Alex comparando
          // contra el Add Account real del PVWA, que también va de borde a
          // borde. Tope de 80rem para no estirar el formulario a lo
          // ridículo en monitores ultra anchos.
          style={{ maxWidth: "min(calc(100vw - 14rem), 80rem)" }}
        >
          <SheetTitle className="sr-only">Crear cuenta</SheetTitle>

          <div className="sticky top-0 z-10 border-b border-border bg-card px-6 py-4">
            <h1 className="text-lg font-bold text-navy-900">Crear cuenta</h1>
          </div>

          <div className="flex flex-1 overflow-hidden">
            <StepNav
              step={step}
              systemType={systemType}
              platformId={platformId}
              safeName={safeName}
              onJump={setStep}
            />
            {/* el panel llega hasta el sidebar, pero el contenido se queda
                en un ancho legible -- mismo criterio visual que el Add
                Account real: la página es ancha, el formulario no. */}
            <div className="flex-1 overflow-y-auto px-6 py-6">
              <div className="max-w-2xl">
                {step === 1 && (
                  <div className="flex animate-in flex-col gap-2 fade-in-0 duration-150">
                    <p className="text-xs text-muted-foreground">Tipo de sistema</p>
                    {platforms.isLoading && (
                      <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                        <Loader2 className="size-4 animate-spin" />
                        Cargando plataformas del tenant…
                      </div>
                    )}
                    <div className="grid grid-cols-4 gap-2">
                      {systemTypes.map((s) => {
                        const Icon = systemTypeIcon(s);
                        return (
                          <button
                            key={s}
                            onClick={() => {
                              // Si el operador vuelve atrás con el stepper y
                              // elige OTRO tipo de sistema, las propiedades
                              // sugeridas de la selección anterior (ej.
                              // "Log On To"/"User DN" de Windows) ya no
                              // aplican -- sin este reset quedaban pegadas
                              // porque `seedSuggestedProps` sólo precarga si
                              // la lista está vacía. Visto real: eligiendo
                              // *NIX después de haber pasado por Windows.
                              if (s !== systemType) setCustomProps([]);
                              setSystemType(s);
                              setStep(2);
                            }}
                            className="flex flex-col items-center gap-2 rounded-lg border border-border px-2 py-4 text-center hover:border-primary hover:bg-muted"
                          >
                            <Icon className="size-8 text-sky-600" />
                            <span className="text-xs font-medium">{s}</span>
                          </button>
                        );
                      })}
                    </div>
                    {platforms.data && systemTypes.length === 0 && (
                      <p className="text-sm text-muted-foreground">
                        No hay plataformas activas en este tenant.
                      </p>
                    )}
                  </div>
                )}

                {step === 2 && (
                  <div className="flex animate-in flex-col gap-2 fade-in-0 duration-150">
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      {(() => {
                        const Icon = systemTypeIcon(systemType);
                        return <Icon className="size-3.5 text-sky-600" />;
                      })()}
                      Plataforma ({systemType})
                    </p>
                    <div className="relative">
                      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        autoFocus
                        className="h-9 pl-8 text-sm"
                        placeholder="Buscar plataforma…"
                        value={platformSearch}
                        onChange={(e) => setPlatformSearch(e.target.value)}
                      />
                    </div>
                    <ul className="max-h-80 divide-y divide-border overflow-y-auto rounded-md border border-border bg-card">
                      {filteredPlatforms.map((p) => (
                        <li key={p.general?.id}>
                          <button
                            onClick={() => {
                              setPlatformId(p.general?.id ?? "");
                              setStep(3);
                            }}
                            className="flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-muted"
                          >
                            <span className="font-medium">{p.general?.name ?? p.general?.id}</span>
                            <span className="text-xs text-muted-foreground">{p.general?.id}</span>
                          </button>
                        </li>
                      ))}
                      {filteredPlatforms.length === 0 && (
                        <li className="px-3 py-2 text-sm text-muted-foreground">Sin resultados.</li>
                      )}
                    </ul>
                  </div>
                )}

                {step === 3 && (
                  <div className="flex animate-in flex-col gap-2 fade-in-0 duration-150">
                    <p className="text-xs text-muted-foreground">Safe</p>
                    <div className="relative">
                      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        autoFocus
                        className="h-9 pl-8 text-sm"
                        placeholder="Buscar safe…"
                        value={safeSearch}
                        onChange={(e) => setSafeSearch(e.target.value)}
                      />
                    </div>
                    <ul className="max-h-80 divide-y divide-border overflow-y-auto rounded-md border border-border bg-card">
                      {filteredSafes.map((n) => (
                        <li key={n}>
                          <button
                            onClick={() => {
                              setSafeName(n);
                              seedSuggestedProps();
                              setStep(4);
                            }}
                            className="block w-full px-3 py-2 text-left text-sm hover:bg-muted"
                          >
                            {n}
                          </button>
                        </li>
                      ))}
                      {filteredSafes.length === 0 && (
                        <li className="px-3 py-2 text-sm text-muted-foreground">Sin resultados.</li>
                      )}
                    </ul>
                  </div>
                )}

                {step === 4 && (
                  <div className="flex animate-in flex-col gap-3 fade-in-0 duration-150">
                    <p className="text-xs text-muted-foreground">
                      {systemType} · {platformId} · safe {safeName}
                    </p>
                    <div className="grid grid-cols-2 gap-3">
                      <label className="text-xs text-muted-foreground">
                        Address *
                        <Input
                          className="mt-1 h-8 text-sm"
                          value={address}
                          onChange={(e) => setAddress(e.target.value)}
                        />
                      </label>
                      <label className="text-xs text-muted-foreground">
                        User Name *
                        <Input
                          className="mt-1 h-8 text-sm"
                          value={userName}
                          onChange={(e) => setUserName(e.target.value)}
                        />
                      </label>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <label className="text-xs text-muted-foreground">
                        Password (opcional)
                        <Input
                          type="password"
                          className="mt-1 h-8 text-sm"
                          value={secret}
                          onChange={(e) => setSecret(e.target.value)}
                        />
                      </label>
                      <label className="text-xs text-muted-foreground">
                        Confirm Password
                        <Input
                          type="password"
                          className="mt-1 h-8 text-sm"
                          value={confirmSecret}
                          onChange={(e) => setConfirmSecret(e.target.value)}
                        />
                      </label>
                    </div>
                    {!secretsOk && (
                      <p className="text-xs text-destructive">Las contraseñas no coinciden.</p>
                    )}

                    <div className="flex items-center justify-between gap-2">
                      <label htmlFor="customize-name" className="text-xs text-muted-foreground">
                        Customize account name
                      </label>
                      <Switch
                        id="customize-name"
                        checked={customizeName}
                        onCheckedChange={setCustomizeName}
                      />
                    </div>
                    {customizeName && (
                      <label className="text-xs text-muted-foreground">
                        Account Name
                        <Input
                          className="mt-1 h-8 text-sm"
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                        />
                      </label>
                    )}

                    <div className="border-t border-border pt-3">
                      <p className="mb-2 text-xs font-semibold text-muted-foreground">
                        Additional Properties
                      </p>
                      <div className="mb-2 flex flex-wrap gap-1.5">
                        {suggestedProperties(systemType)
                          .filter((s) => !customProps.some((r) => r.key.toLowerCase() === s.toLowerCase()))
                          .map((s) => (
                            <button
                              key={s}
                              type="button"
                              onClick={() => addPropertyRow(s)}
                              className="rounded-full border border-dashed border-border px-2.5 py-0.5 text-xs text-muted-foreground hover:border-primary hover:text-foreground"
                            >
                              + {s}
                            </button>
                          ))}
                      </div>
                      <div className="flex flex-col gap-2">
                        {customProps.map((row) => (
                          <div key={row.id} className="flex items-center gap-2">
                            <Input
                              className="h-8 flex-1 text-sm"
                              placeholder="Nombre (ej. Log On To)"
                              value={row.key}
                              onChange={(e) => updatePropertyRow(row.id, { key: e.target.value })}
                            />
                            <Input
                              className="h-8 flex-1 text-sm"
                              placeholder="Valor"
                              value={row.value}
                              onChange={(e) => updatePropertyRow(row.id, { value: e.target.value })}
                            />
                            <button
                              type="button"
                              onClick={() => removePropertyRow(row.id)}
                              aria-label="Quitar propiedad"
                              className="shrink-0 text-muted-foreground hover:text-destructive"
                            >
                              <X className="size-4" />
                            </button>
                          </div>
                        ))}
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="mt-1"
                        onClick={() => addPropertyRow()}
                      >
                        <Plus className="size-3.5" />
                        Agregar propiedad
                      </Button>
                    </div>

                    <div className="flex flex-col gap-3 border-t border-border pt-3">
                      <span className="text-xs text-muted-foreground">
                        Remote machine access (opcional)
                      </span>
                      <div className="flex items-center justify-between gap-2">
                        <label htmlFor="restrict-remote" className="cursor-pointer text-xs text-muted-foreground">
                          Limit access to these machines only
                        </label>
                        <Switch
                          id="restrict-remote"
                          checked={restrictRemote}
                          onCheckedChange={setRestrictRemote}
                        />
                      </div>
                      <textarea
                        className="w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
                        rows={3}
                        placeholder="10.1.2.1&#10;10.1.2.2"
                        value={remoteMachines}
                        onChange={(e) => setRemoteMachines(e.target.value)}
                      />
                    </div>

                    <div className="border-t border-border pt-3">
                      <p className="mb-2 text-xs font-semibold text-muted-foreground">
                        Account management
                      </p>
                      <div className="flex items-center justify-between gap-2">
                        <label htmlFor="auto-management" className="text-xs text-muted-foreground">
                          Allow automatic password management
                        </label>
                        <Switch
                          id="auto-management"
                          checked={autoManagement}
                          onCheckedChange={setAutoManagement}
                        />
                      </div>
                      {!autoManagement && (
                        <label className="mt-2 block text-xs text-muted-foreground">
                          Motivo (obligatorio al deshabilitar la gestión automática) *
                          <Input
                            className="mt-1 h-8 text-sm"
                            value={manualReason}
                            onChange={(e) => setManualReason(e.target.value)}
                          />
                        </label>
                      )}
                    </div>

                    <div className="flex justify-end gap-2">
                      <Button size="sm" disabled={!canPreview} onClick={preview}>
                        Previsualizar
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </SheetContent>
      </Sheet>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/accounts`}
        changes={dialog ?? {}}
        title="Crear cuenta"
        onDone={() => {
          reset();
          qc.invalidateQueries({ queryKey: ["inv", tenant, "accounts"] });
        }}
      />
    </>
  );
}
