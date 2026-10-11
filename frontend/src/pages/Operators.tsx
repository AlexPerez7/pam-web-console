import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  OPERATOR_ROLES,
  type Assignment,
  type OperatorAdmin,
  type OperatorRole,
  type TenantSummary,
} from "@/lib/types";
import { toast } from "@/components/ui/toast";

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "error";
}

export default function Operators() {
  const { operator: me } = useAuth();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["operators"],
    queryFn: () => api<OperatorAdmin[]>("/operators"),
  });

  const [banner, setBanner] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [pwdFor, setPwdFor] = useState<OperatorAdmin | null>(null);
  const [editFor, setEditFor] = useState<OperatorAdmin | null>(null);
  const [assignFor, setAssignFor] = useState<OperatorAdmin | null>(null);
  const reload = () => qc.invalidateQueries({ queryKey: ["operators"] });

  const patch = useMutation({
    mutationFn: (v: { id: number; body: Record<string, unknown> }) =>
      api(`/operators/${v.id}`, { method: "PATCH", body: v.body }),
    onSuccess: () => {
      setBanner(null);
      reload();
      toast({ title: "Operador actualizado" });
    },
    onError: (e) => setBanner(errMsg(e)),
  });

  const resetTotp = useMutation({
    mutationFn: (id: number) => api(`/operators/${id}/reset-totp`, { method: "POST" }),
    onSuccess: () => {
      setBanner(null);
      toast({
        title: "TOTP reseteado",
        description:
          "El operador lo vuelve a enrolar en el próximo login; sus sesiones activas se cerraron.",
      });
      reload();
    },
    onError: (e) => setBanner(errMsg(e)),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-[22px] font-bold text-navy-900">Operadores</h1>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          Nuevo operador
        </Button>
      </div>

      {banner && (
        <Alert variant="info" className="flex items-start justify-between gap-4">
          <span>{banner}</span>
          <button
            className="text-xs underline"
            onClick={() => setBanner(null)}
          >
            cerrar
          </button>
        </Alert>
      )}
      {list.isError && <Alert variant="destructive">{errMsg(list.error)}</Alert>}
      {list.isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          <span>Cargando…</span>
        </div>
      )}

      {list.data && (
        <div className="rounded-lg border border-border">
          <Table style={{ minWidth: 900 }}>
            <TableHeader>
              <TableRow>
                <TableHead>Usuario</TableHead>
                <TableHead>Nombre</TableHead>
                <TableHead>Rol</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>TOTP</TableHead>
                <TableHead>Acciones</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.data.map((o) => {
                const self = o.id === me?.id;
                return (
                  <TableRow key={o.id}>
                    <TableCell>
                      {o.username}
                      {self && (
                        <span className="text-xs text-muted-foreground"> (usted)</span>
                      )}
                    </TableCell>
                    <TableCell>{o.display_name ?? "—"}</TableCell>
                    <TableCell>
                      <Select
                        value={o.role}
                        disabled={self || patch.isPending}
                        onValueChange={(v) => patch.mutate({ id: o.id, body: { role: v } })}
                      >
                        <SelectTrigger aria-label={`Rol de ${o.username}`} className="h-8 w-40 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {OPERATOR_ROLES.map((r) => (
                            <SelectItem key={r} value={r}>
                              {r}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      {o.is_active ? (
                        <Badge variant="success">activo</Badge>
                      ) : (
                        <Badge variant="danger">inactivo</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {o.totp_enrolled ? (
                        <Badge variant="info">enrolado</Badge>
                      ) : (
                        <Badge variant="neutral">pendiente</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" variant="outline" onClick={() => setEditFor(o)}>
                          Editar
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => setPwdFor(o)}>
                          Contraseña
                        </Button>
                        {o.role !== "platform_admin" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => setAssignFor(o)}
                          >
                            Accesos
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!o.totp_enrolled}
                          onClick={() => resetTotp.mutate(o.id)}
                        >
                          Reset TOTP
                        </Button>
                        {!self && (
                          <Button
                            size="sm"
                            variant="outline"
                            className={o.is_active ? "text-danger-ink" : "text-line-transformation"}
                            onClick={() =>
                              patch.mutate({ id: o.id, body: { is_active: !o.is_active } })
                            }
                          >
                            {o.is_active ? "Desactivar" : "Activar"}
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <CreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onDone={() => {
          setBanner(
            "Operador creado. Entregue la contraseña temporal; el operador la cambia en el primer login.",
          );
          reload();
        }}
      />
      <EditDialog
        target={editFor}
        onClose={() => setEditFor(null)}
        onDone={() => {
          setBanner("Operador actualizado.");
          setEditFor(null);
          reload();
        }}
      />
      <ResetPasswordDialog
        target={pwdFor}
        onClose={() => setPwdFor(null)}
        onDone={() => {
          setBanner("Contraseña reseteada. El operador la cambia en el próximo login.");
          setPwdFor(null);
        }}
      />
      <AssignmentsDialog
        target={assignFor}
        onClose={() => setAssignFor(null)}
        onDone={() => {
          setBanner("Accesos por tenant actualizados.");
          setAssignFor(null);
        }}
      />
    </div>
  );
}

const NO_ACCESS = "none";

function AssignmentsDialog({
  target,
  onClose,
  onDone,
}: {
  target: OperatorAdmin | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
    enabled: !!target,
  });
  const current = useQuery({
    queryKey: ["assignments", target?.id],
    queryFn: () => api<Assignment[]>(`/operators/${target!.id}/assignments`),
    enabled: !!target,
  });

  const [roles, setRoles] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (current.data) {
      const m: Record<string, string> = {};
      for (const a of current.data) m[a.tenant_id] = a.role;
      setRoles(m);
    }
  }, [current.data]);

  const m = useMutation({
    mutationFn: () =>
      api(`/operators/${target!.id}/assignments`, {
        method: "PUT",
        body: {
          assignments: Object.entries(roles)
            .filter(([, r]) => r !== NO_ACCESS)
            .map(([tenant_id, role]) => ({ tenant_id, role })),
        },
      }),
    onSuccess: () => {
      setErr(null);
      toast({ title: "Asignaciones guardadas", description: target?.username });
      onDone();
    },
    onError: (e) => setErr(errMsg(e)),
  });

  const rows = [
    { id: "*", name: "Todos los tenants (*)" },
    ...(tenants.data ?? []).map((t) => ({ id: t.id, name: t.name })),
  ];

  return (
    <Dialog open={!!target} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogTitle>
          {target ? `Accesos de ${target.username}` : ""}
        </DialogTitle>
        <p className="text-xs text-muted-foreground">
          El rol efectivo del operador en cada tenant. "Sin acceso" = no ve ese
          tenant. "Todos los tenants (*)" es el fallback si no hay uno específico.
        </p>
        {current.isLoading && <Loader2 className="size-4 animate-spin" />}
        <div className="flex max-h-80 flex-col gap-2 overflow-y-auto">
          {rows.map((r) => (
            <div key={r.id} className="flex items-center justify-between gap-3">
              <span className="text-sm">{r.name}</span>
              <Select
                value={roles[r.id] ?? NO_ACCESS}
                onValueChange={(v) => setRoles((s) => ({ ...s, [r.id]: v }))}
              >
                <SelectTrigger aria-label={`Rol en ${r.name}`} className="h-8 w-40 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_ACCESS}>sin acceso</SelectItem>
                  {OPERATOR_ROLES.map((role) => (
                    <SelectItem key={role} value={role}>
                      {role}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>
        {err && <Alert variant="destructive">{err}</Alert>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancelar
          </Button>
          <Button disabled={m.isPending} onClick={() => m.mutate()}>
            {m.isPending && <Loader2 className="animate-spin" />}
            Guardar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function CreateDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDone: () => void;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OperatorRole>("viewer");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const m = useMutation({
    mutationFn: () =>
      api("/operators", {
        method: "POST",
        body: {
          username: username.trim(),
          password,
          role,
          display_name: displayName.trim() || null,
          email: email.trim() || null,
        },
      }),
    onSuccess: () => {
      setUsername("");
      setDisplayName("");
      setEmail("");
      setRole("viewer");
      setPassword("");
      setErr(null);
      onOpenChange(false);
      toast({ title: "Operador creado", description: "Deberá cambiar la contraseña en el primer login." });
      onDone();
    },
    onError: (e) => setErr(errMsg(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Nuevo operador</DialogTitle>
        <Field label="Usuario">
          <Input value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Nombre">
          <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </Field>
        <Field label="Email">
          <Input value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Rol">
          <Select value={role} onValueChange={(v) => setRole(v as OperatorRole)}>
            <SelectTrigger aria-label="Rol">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OPERATOR_ROLES.map((r) => (
                <SelectItem key={r} value={r}>
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field
          label="Contraseña temporal"
          hint="mínimo 12 caracteres · el operador la cambia en el primer login"
        >
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {err && <Alert variant="destructive">{err}</Alert>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={m.isPending || !username.trim() || password.length < 12}
            onClick={() => m.mutate()}
          >
            {m.isPending && <Loader2 className="animate-spin" />}
            Crear
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditDialog({
  target,
  onClose,
  onDone,
}: {
  target: OperatorAdmin | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (target) {
      setUsername(target.username);
      setDisplayName(target.display_name ?? "");
      setEmail(target.email ?? "");
      setErr(null);
    }
  }, [target]);

  const m = useMutation({
    mutationFn: () =>
      api(`/operators/${target!.id}`, {
        method: "PATCH",
        body: {
          username: username.trim(),
          display_name: displayName.trim(),
          email: email.trim(),
        },
      }),
    onSuccess: () => {
      setErr(null);
      toast({ title: "Operador actualizado", description: username.trim() });
      onDone();
    },
    onError: (e) => setErr(errMsg(e)),
  });

  return (
    <Dialog open={!!target} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogTitle>{target ? `Editar ${target.username}` : ""}</DialogTitle>
        <Field label="Usuario" hint="usado para iniciar sesión — avisar al operador si se cambia">
          <Input value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Nombre">
          <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </Field>
        <Field label="Email">
          <Input value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        {err && <Alert variant="destructive">{err}</Alert>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancelar
          </Button>
          <Button disabled={m.isPending || !username.trim()} onClick={() => m.mutate()}>
            {m.isPending && <Loader2 className="animate-spin" />}
            Guardar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ResetPasswordDialog({
  target,
  onClose,
  onDone,
}: {
  target: OperatorAdmin | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const m = useMutation({
    mutationFn: () =>
      api(`/operators/${target!.id}/reset-password`, {
        method: "POST",
        body: { new_password: password },
      }),
    onSuccess: () => {
      setPassword("");
      setErr(null);
      toast({ title: "Contraseña reseteada", description: target?.username });
      onDone();
    },
    onError: (e) => setErr(errMsg(e)),
  });

  return (
    <Dialog open={!!target} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogTitle>
          {target ? `Resetear contraseña de ${target.username}` : ""}
        </DialogTitle>
        <Field
          label="Contraseña temporal"
          hint="mínimo 12 caracteres · el operador la cambia en el próximo login"
        >
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {err && <Alert variant="destructive">{err}</Alert>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancelar
          </Button>
          <Button disabled={m.isPending || password.length < 12} onClick={() => m.mutate()}>
            {m.isPending && <Loader2 className="animate-spin" />}
            Resetear
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
