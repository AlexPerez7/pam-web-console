import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { useAuth } from "@/lib/auth";

// Cierre de sesión por inactividad (docs/specs/done/session-hardening.md).
// La última actividad (teclado/mouse/scroll) se guarda en localStorage para
// compartirla entre pestañas: trabajar en una mantiene viva la sesión de
// todas. Es sólo un timestamp, nunca un token. El server tiene su propio
// tope (session_idle_minutes), esto es el aviso amable en la UI.
const IDLE_MS = 30 * 60 * 1000;
const WARN_MS = 60 * 1000;
const KEY = "pam:last-activity";
const EVENTS = ["mousedown", "keydown", "wheel", "touchstart", "mousemove"] as const;

function readLast(): number {
  try {
    const v = Number(localStorage.getItem(KEY));
    return Number.isFinite(v) && v > 0 ? v : Date.now();
  } catch {
    return Date.now();
  }
}

function writeLast(ts: number) {
  try {
    localStorage.setItem(KEY, String(ts));
  } catch {
    /* sin storage: el tope del server sigue aplicando */
  }
}

export function IdleGuard() {
  const { logout } = useAuth();
  const [remaining, setRemaining] = useState<number | null>(null);
  const lastWrite = useRef(0);
  const loggingOut = useRef(false);
  // ref para no reiniciar los listeners/timer cada vez que cambia la
  // identidad de `logout` (se recrea en cada render del AuthProvider)
  const logoutRef = useRef(logout);
  useEffect(() => {
    logoutRef.current = logout;
  }, [logout]);

  useEffect(() => {
    writeLast(Date.now());
    const onActivity = () => {
      const now = Date.now();
      // throttle: como mucho una escritura cada 5 s
      if (now - lastWrite.current > 5000) {
        lastWrite.current = now;
        writeLast(now);
      }
    };
    for (const e of EVENTS) window.addEventListener(e, onActivity, { passive: true });

    const tick = setInterval(() => {
      const idle = Date.now() - readLast();
      if (idle >= IDLE_MS) {
        if (!loggingOut.current) {
          loggingOut.current = true;
          void logoutRef.current("current", "inactividad");
        }
      } else if (idle >= IDLE_MS - WARN_MS) {
        setRemaining(Math.ceil((IDLE_MS - idle) / 1000));
      } else {
        setRemaining(null);
      }
    }, 1000);

    return () => {
      for (const e of EVENTS) window.removeEventListener(e, onActivity);
      clearInterval(tick);
    };
  }, []);

  return (
    <Dialog open={remaining !== null}>
      <DialogContent>
        <DialogTitle>Su sesión está por cerrarse</DialogTitle>
        <p className="text-sm text-muted-foreground">
          Por inactividad, la sesión se cerrará en{" "}
          <span className="font-semibold text-foreground">{remaining ?? 0} s</span>.
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={() => void logout("current")}>
            Salir ahora
          </Button>
          <Button
            onClick={() => {
              writeLast(Date.now());
              setRemaining(null);
            }}
          >
            Seguir conectado
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
