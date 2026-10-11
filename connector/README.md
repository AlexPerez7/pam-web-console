# connector — Conector saliente para PVWA on-prem

*(Futuro. Self-Hosted está parkeado en v1 — ver `docs/PLAN.md` §9.3. Vacío
hasta que se desparkee.)*

Servicio liviano que se despliega **en la red de cada cliente Self-Hosted**
y hace de puente entre PAM Web Console (on-prem en el MSP) y el PVWA on-prem del
cliente, cuando no hay VPN site-to-site. Alternativa a la VPN, no
obligatorio.

## Requisitos de diseño

- **Solo tráfico saliente** desde la red del cliente hacia PAM Web Console (el cliente
  no abre puertos entrantes).
- No almacena secretos: recibe la operación ya autorizada por PAM Web Console y la
  ejecuta contra el PVWA local.
- Mismo patrón que usa el propio CyberArk con Connector Management / SIA.
- Autenticado mutuamente con el plano de control (mTLS / token de conector).
- Registra todo lo que pasa y lo reporta al audit log central.
