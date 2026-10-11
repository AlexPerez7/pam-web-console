import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Copy,
  Eye,
  EyeOff,
  FileCheck2,
  KeyRound,
  Loader2,
  ShieldCheck,
  Users,
} from "lucide-react";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import isotipo from "@/assets/brand/isotipo.svg";
import { TotpInput } from "@/components/TotpInput";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { LoginResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

// Login en dos pasos (contraseña + TOTP), más el enrolamiento del TOTP en el
// primer ingreso. Diseño "La Sala de Control" (DESIGN.md): panel navy plano
// a la izquierda con la identidad y las garantías del producto, formulario
// a la derecha. Sin gradientes ni ilustraciones; sky sólo para el paso
// activo y el foco.

type Phase = "cred" | "enroll" | "totp";

const REASONS: Record<string, string> = {
  inactividad: "Su sesión se cerró por inactividad. Vuelva a ingresar para continuar.",
};

function friendlyError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401 && /TOTP/i.test(e.message))
      return "El código no es válido o ya fue usado. Espere el siguiente código de su aplicación.";
    if (e.status === 401) return "Usuario o contraseña incorrectos.";
    if (e.status === 429) return e.message.charAt(0).toUpperCase() + e.message.slice(1) + ".";
    if (e.status >= 500) return "El servicio no está disponible en este momento. Intente de nuevo en unos minutos.";
    return e.message;
  }
  return "No se pudo conectar con PAM Web Console. Verifique su conexión.";
}

/** Clave TOTP en grupos de 4 para leerla/tipearla a mano sin errores. */
function groupSecret(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}

function Steps({ phase }: { phase: Phase }) {
  const second = phase === "enroll" ? "Configurar MFA" : "Verificación";
  const steps = [
    { label: "Credenciales", done: phase !== "cred", active: phase === "cred" },
    { label: second, done: false, active: phase !== "cred" },
  ];
  return (
    <ol className="flex items-center gap-2 text-xs" aria-label="Pasos de inicio de sesión">
      {steps.map((s, i) => (
        <li key={s.label} className="flex items-center gap-2">
          {i > 0 && <span className="h-px w-6 bg-border" aria-hidden="true" />}
          <span
            aria-current={s.active ? "step" : undefined}
            className={cn(
              "flex size-5 items-center justify-center rounded-sm text-[11px] font-semibold",
              s.active && "bg-sky-500 text-navy-900",
              s.done && "bg-navy-900 text-white",
              !s.active && !s.done && "bg-muted text-muted-foreground",
            )}
          >
            {s.done ? <Check className="size-3" aria-hidden="true" /> : i + 1}
          </span>
          <span className={cn(s.active ? "font-semibold text-foreground" : "text-muted-foreground")}>
            {s.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

function BrandPanel() {
  const points: Array<{ icon: typeof ShieldCheck; title: string; text: string }> = [
    {
      icon: ShieldCheck,
      title: "Acceso con doble factor",
      text: "Contraseña y código TOTP en cada ingreso, sesiones cortas y cierre por inactividad.",
    },
    {
      icon: FileCheck2,
      title: "Todo queda auditado",
      text: "Cada lectura y cada cambio se registra a nombre del operador, en una bitácora encadenada.",
    },
    {
      icon: Users,
      title: "Cuatro ojos en lo crítico",
      text: "Las operaciones destructivas requieren la aprobación de un segundo operador.",
    },
  ];
  return (
    <aside className="relative hidden w-[44%] max-w-xl shrink-0 flex-col justify-between bg-navy-900 px-12 py-10 text-white lg:flex">
      <div className="flex items-center gap-3">
        <img src={isotipo} alt="" className="h-8 w-auto object-contain" />
        <div className="leading-tight">
          <div className="text-sm font-bold tracking-wide">PAM Web Console</div>
          <div className="text-[11px] text-sky-100">Consola de operación PAM</div>
        </div>
      </div>

      <div className="flex flex-col gap-8">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-sky-300">
            Operación PAM multi-tenant
          </p>
          <p className="mt-3 max-w-sm text-[22px] font-bold leading-snug">
            Una sola consola para operar CyberArk en los tenants de todos los clientes.
          </p>
        </div>
        <ul className="flex flex-col gap-5">
          {points.map((p) => (
            <li key={p.title} className="flex gap-3">
              <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-navy-800">
                <p.icon className="size-4 text-sky-300" aria-hidden="true" />
              </span>
              <div>
                <p className="text-sm font-semibold">{p.title}</p>
                <p className="mt-0.5 text-sm text-sky-100">{p.text}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <p className="text-[11px] text-sky-100">
        PAM Web Console · proyecto de portfolio
      </p>
    </aside>
  );
}

function PasswordField({
  value,
  onChange,
  describedBy,
}: {
  value: string;
  onChange: (v: string) => void;
  describedBy?: string;
}) {
  const [visible, setVisible] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const capsId = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor="password">Contraseña</Label>
      <div className="relative">
        <Input
          id="password"
          name="password"
          type={visible ? "text" : "password"}
          autoComplete="current-password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyUp={(e) => setCapsLock(e.getModifierState("CapsLock"))}
          onBlur={() => setCapsLock(false)}
          aria-describedby={[describedBy, capsLock ? capsId : ""].filter(Boolean).join(" ") || undefined}
          className="pr-10"
          required
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-md text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={visible ? "Ocultar contraseña" : "Mostrar contraseña"}
          aria-pressed={visible}
        >
          {visible ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
        </button>
      </div>
      {capsLock && (
        <p id={capsId} className="flex items-center gap-1.5 text-xs text-warning-ink">
          <AlertTriangle className="size-3.5" aria-hidden="true" />
          Bloq Mayús está activado.
        </p>
      )}
    </div>
  );
}

function SubmitButton({ busy, children, disabled }: { busy: boolean; children: ReactNode; disabled?: boolean }) {
  return (
    <Button type="submit" className="h-10 w-full" disabled={busy || disabled}>
      {busy && <Loader2 className="animate-spin" aria-hidden="true" />}
      {children}
    </Button>
  );
}

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [params] = useSearchParams();
  const notice = REASONS[params.get("motivo") ?? ""];
  const from = (loc.state as { from?: { pathname: string; search?: string } } | null)?.from;

  const [phase, setPhase] = useState<Phase>("cred");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [enroll, setEnroll] = useState<{ qr: string; secret: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const errId = useId();
  const hintId = useId();

  const [environment, setEnvironment] = useState<string | null>(null);
  useEffect(() => {
    api<{ environment?: string }>("/version", { auth: false })
      .then((v) => setEnvironment(v.environment ?? null))
      .catch(() => setEnvironment(null));
  }, []);

  async function submit(code?: string) {
    setErr(null);
    setBusy(true);
    try {
      const body: Record<string, string> = { username: username.trim(), password };
      if (code) body.totp_code = code;
      const r = await api<LoginResponse>("/auth/login", { method: "POST", body, auth: false });
      if (r.status === "enroll") {
        setEnroll({ qr: r.qr, secret: r.secret });
        setPhase("enroll");
      } else if (r.status === "totp_required") {
        setPhase("totp");
      } else {
        login(r.access_token, r.operator);
        // vuelve a la página que se intentó abrir antes del login
        const target = from && from.pathname !== "/login" ? `${from.pathname}${from.search ?? ""}` : "/";
        nav(target, { replace: true });
      }
    } catch (e) {
      setErr(friendlyError(e));
      if (code) setTotp(""); // código rechazado: limpiar para tipear el siguiente
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (phase === "cred") {
      if (username.trim() && password) void submit();
    } else if (totp.length === 6) {
      void submit(totp);
    }
  }

  function backToCredentials() {
    setPhase("cred");
    setTotp("");
    setErr(null);
    setEnroll(null);
    setPassword("");
  }

  async function copySecret() {
    if (!enroll) return;
    try {
      await navigator.clipboard.writeText(enroll.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* sin portapapeles (contexto no seguro): la clave sigue visible para copiar a mano */
    }
  }

  const title =
    phase === "cred" ? "Iniciar sesión" : phase === "enroll" ? "Configure su doble factor" : "Verificación en dos pasos";

  return (
    <div className="flex min-h-screen bg-background">
      <BrandPanel />

      <main className="flex flex-1 flex-col">
        {/* marca compacta cuando el panel izquierdo no entra (< lg) */}
        <div className="flex items-center gap-2.5 bg-navy-900 px-5 py-4 text-white lg:hidden">
          <img src={isotipo} alt="" className="h-6 w-auto object-contain" />
          <span className="text-sm font-bold tracking-wide">PAM Web Console</span>
          <span className="text-[11px] text-sky-100">PAM Web Console</span>
        </div>

        <div className="flex flex-1 items-center justify-center px-4 py-10">
          <div className="w-full max-w-sm">
            <div className="mb-6 flex items-center justify-between gap-3">
              <Steps phase={phase} />
              {environment && environment !== "prod" && (
                <span className="rounded-sm bg-[#fdf0d6] px-2 py-0.5 text-xs font-medium text-warning-ink">
                  Entorno: {environment}
                </span>
              )}
            </div>

            <h1 className="text-[22px] font-bold text-navy-900">{title}</h1>

            <form onSubmit={onSubmit} className="mt-5 flex flex-col gap-4" noValidate>
              {phase === "cred" && (
                <>
                  <p className="-mt-2 text-sm text-muted-foreground">
                    Ingrese con su usuario de operador de PAM Web Console.
                  </p>
                  {notice && (
                    <Alert variant="info">
                      <p className="text-sm">{notice}</p>
                    </Alert>
                  )}
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="username">Usuario</Label>
                    <Input
                      id="username"
                      name="username"
                      autoComplete="username"
                      autoCapitalize="none"
                      spellCheck={false}
                      autoFocus
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      aria-describedby={err ? errId : undefined}
                      aria-invalid={!!err || undefined}
                      required
                    />
                  </div>
                  <PasswordField
                    value={password}
                    onChange={setPassword}
                    describedBy={err ? errId : undefined}
                  />
                  <SubmitButton busy={busy} disabled={!username.trim() || !password}>
                    {busy ? "Verificando…" : "Continuar"}
                  </SubmitButton>
                </>
              )}

              {phase === "totp" && (
                <>
                  <p id={hintId} className="-mt-2 text-sm text-muted-foreground">
                    Ingrese el código de 6 dígitos de su aplicación de autenticación para{" "}
                    <span className="font-semibold text-foreground">{username.trim()}</span>.
                  </p>
                  <TotpInput
                    value={totp}
                    onChange={setTotp}
                    onComplete={(c) => !busy && void submit(c)}
                    disabled={busy}
                    invalid={!!err}
                    autoFocus
                    describedBy={err ? `${hintId} ${errId}` : hintId}
                  />
                  <SubmitButton busy={busy} disabled={totp.length !== 6}>
                    {busy ? "Verificando…" : "Ingresar"}
                  </SubmitButton>
                </>
              )}

              {phase === "enroll" && enroll && (
                <>
                  <p className="-mt-2 text-sm text-muted-foreground">
                    Es su primer ingreso: vincule PAM Web Console con una aplicación de autenticación. Lo
                    hace una sola vez.
                  </p>
                  <ol className="flex flex-col gap-4 text-sm">
                    <li className="flex gap-3">
                      <StepNumber n={1} />
                      <span>
                        Abra Microsoft Authenticator, Google Authenticator u otra aplicación TOTP en su
                        teléfono.
                      </span>
                    </li>
                    <li className="flex gap-3">
                      <StepNumber n={2} />
                      <div className="flex flex-col gap-3">
                        <span>Escanee este código QR.</span>
                        <img
                          src={enroll.qr}
                          alt="Código QR para configurar la aplicación de autenticación"
                          className="size-40 rounded-lg border border-border bg-white p-2"
                        />
                        <div>
                          <p className="text-xs text-muted-foreground">
                            ¿No puede escanearlo? Ingrese esta clave a mano:
                          </p>
                          <div className="mt-1 flex items-center gap-2">
                            <code className="rounded-sm bg-muted px-2 py-1 font-mono text-xs tracking-wider">
                              {groupSecret(enroll.secret)}
                            </code>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={copySecret}
                              aria-label="Copiar clave"
                            >
                              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                              {copied ? "Copiada" : "Copiar"}
                            </Button>
                          </div>
                        </div>
                      </div>
                    </li>
                    <li className="flex gap-3">
                      <StepNumber n={3} />
                      <div className="flex flex-col gap-3">
                        <span id={hintId}>Ingrese el código de 6 dígitos que muestra la aplicación.</span>
                        <TotpInput
                          value={totp}
                          onChange={setTotp}
                          onComplete={(c) => !busy && void submit(c)}
                          disabled={busy}
                          invalid={!!err}
                          describedBy={err ? `${hintId} ${errId}` : hintId}
                        />
                      </div>
                    </li>
                  </ol>
                  <SubmitButton busy={busy} disabled={totp.length !== 6}>
                    {busy ? "Verificando…" : "Verificar e ingresar"}
                  </SubmitButton>
                </>
              )}

              {err && (
                <Alert variant="destructive" id={errId}>
                  <p className="text-sm">{err}</p>
                </Alert>
              )}

              {phase !== "cred" && (
                <button
                  type="button"
                  onClick={backToCredentials}
                  className="flex items-center gap-1.5 self-start rounded-sm text-sm text-navy-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <ArrowLeft className="size-3.5" aria-hidden="true" />
                  Usar otro usuario
                </button>
              )}
            </form>

            <div className="mt-8 flex gap-2.5 border-t border-border pt-5 text-xs text-muted-foreground">
              <KeyRound className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              <p>
                Sistema de uso restringido a operadores autorizados. Toda la actividad
                queda registrada. Si olvidó su contraseña o perdió el acceso a su aplicación de
                autenticación, pida a un administrador de PAM Web Console que la restablezca.
              </p>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function StepNumber({ n }: { n: number }) {
  return (
    <span
      className="flex size-6 shrink-0 items-center justify-center rounded-sm bg-navy-900 text-xs font-semibold text-white"
      aria-hidden="true"
    >
      {n}
    </span>
  );
}
