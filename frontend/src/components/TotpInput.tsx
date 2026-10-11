import { useRef } from "react";
import { cn } from "@/lib/utils";

const LENGTH = 6;

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** se llama cuando se completan los 6 dígitos (tipeados o pegados) */
  onComplete?: (value: string) => void;
  disabled?: boolean;
  invalid?: boolean;
  autoFocus?: boolean;
  /** id del texto que describe el campo (instrucciones / error) */
  describedBy?: string;
}

/** Código TOTP en 6 casillas. Acepta sólo dígitos, avanza solo, Backspace
 *  retrocede, flechas navegan y pegar un código completo lo reparte entre
 *  las casillas. La primera casilla lleva `autocomplete="one-time-code"`
 *  para que el navegador / gestor de contraseñas pueda completarlo. */
export function TotpInput({
  value,
  onChange,
  onComplete,
  disabled,
  invalid,
  autoFocus,
  describedBy,
}: Props) {
  const refs = useRef<Array<HTMLInputElement | null>>([]);
  const digits = Array.from({ length: LENGTH }, (_, i) => value[i] ?? "");

  function commit(next: string, focusIndex: number) {
    const clean = next.replace(/\D/g, "").slice(0, LENGTH);
    onChange(clean);
    refs.current[Math.min(focusIndex, LENGTH - 1)]?.focus();
    if (clean.length === LENGTH) onComplete?.(clean);
  }

  function setDigit(i: number, raw: string) {
    const d = raw.replace(/\D/g, "");
    if (!d) return;
    if (d.length > 1) {
      // autocompletado del navegador o pegado sobre una casilla
      commit(value.slice(0, i) + d, i + d.length);
      return;
    }
    const arr = digits.slice();
    arr[i] = d;
    commit(arr.join(""), i + 1);
  }

  function onKeyDown(i: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace") {
      e.preventDefault();
      const arr = digits.slice();
      if (arr[i]) {
        arr[i] = "";
        onChange(arr.join("").slice(0, i) + arr.slice(i + 1).join(""));
      } else if (i > 0) {
        arr[i - 1] = "";
        onChange(arr.slice(0, i - 1).join("") + arr.slice(i).join(""));
        refs.current[i - 1]?.focus();
      }
    } else if (e.key === "ArrowLeft" && i > 0) {
      e.preventDefault();
      refs.current[i - 1]?.focus();
    } else if (e.key === "ArrowRight" && i < LENGTH - 1) {
      e.preventDefault();
      refs.current[i + 1]?.focus();
    }
  }

  return (
    <div
      role="group"
      aria-label="Código de verificación de 6 dígitos"
      aria-describedby={describedBy}
      className="flex gap-2"
      onPaste={(e) => {
        const pasted = e.clipboardData.getData("text").replace(/\D/g, "");
        if (!pasted) return;
        e.preventDefault();
        commit(pasted, pasted.length);
      }}
    >
      {digits.map((d, i) => (
        <input
          key={i}
          ref={(el) => {
            refs.current[i] = el;
          }}
          value={d}
          onChange={(e) => setDigit(i, e.target.value)}
          onKeyDown={(e) => onKeyDown(i, e)}
          onFocus={(e) => e.target.select()}
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={i === 0 ? LENGTH : 1}
          autoComplete={i === 0 ? "one-time-code" : "off"}
          autoFocus={autoFocus && i === 0}
          disabled={disabled}
          aria-label={`Dígito ${i + 1} de ${LENGTH}`}
          aria-invalid={invalid || undefined}
          className={cn(
            "h-12 w-11 rounded-md border bg-card text-center text-lg font-semibold tabular-nums text-foreground",
            "transition-colors duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
            "disabled:cursor-not-allowed disabled:opacity-50",
            invalid ? "border-destructive" : "border-input",
          )}
        />
      ))}
    </div>
  );
}
