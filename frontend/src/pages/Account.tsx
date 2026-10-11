import { Loader2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Operator } from "@/lib/types";

interface ChangePasswordResponse {
  access_token: string;
  operator: Operator;
}

export default function Account() {
  const { operator, login } = useAuth();
  const nav = useNavigate();
  const forced = operator?.must_change_password ?? false;

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setErr(null);
    setOk(false);
    if (next !== repeat) {
      setErr("la contraseña nueva y su repetición no coinciden");
      return;
    }
    if (next.length < 12) {
      setErr("la contraseña debe tener al menos 12 caracteres");
      return;
    }
    setBusy(true);
    try {
      const res = await api<ChangePasswordResponse>("/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      // el cambio de contraseña invalida todas las sesiones anteriores del
      // operador (ver docs/specs/done/login-hardening.md) -- el server devuelve
      // tokens nuevos para que esta sesión siga andando sin recargar.
      login(res.access_token, res.operator);
      setOk(true);
      setCurrent("");
      setNext("");
      setRepeat("");
      if (forced) nav("/", { replace: true });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "error al cambiar la contraseña");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex max-w-[460px] flex-col gap-4">
      <div>
        <h1 className="text-[22px] font-bold text-navy-900">Mi cuenta</h1>
        <p className="text-sm text-muted-foreground">
          {operator?.username} · {operator?.role}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Cambiar contraseña</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {forced && (
            <Alert variant="warning">
              Debe cambiar la contraseña asignada antes de continuar.
            </Alert>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cur">Contraseña actual</Label>
            <Input
              id="cur"
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new">Contraseña nueva</Label>
            <Input
              id="new"
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">mínimo 12 caracteres</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rep">Repetir contraseña nueva</Label>
            <Input
              id="rep"
              type="password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
            />
          </div>
          {err && <Alert variant="destructive">{err}</Alert>}
          {ok && <Alert variant="info">Contraseña actualizada.</Alert>}
          <Button onClick={submit} disabled={busy || !current || !next || !repeat}>
            {busy && <Loader2 className="animate-spin" />}
            Cambiar contraseña
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
