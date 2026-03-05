import { useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";

const API_URL =
  "https://script.google.com/macros/s/AKfycbwx0V46aQJP0Zjo7g6t-YaHgJzndSa48kGV7mR-WSnlDabcr2ybAS6MHbWk46Lso4ri/exec";

/**
 * LOGO:
 * - Mantiene single-file, pero sin base64 gigante en este mensaje.
 * - Si ya tienes LOGO_HZ base64 en tu proyecto, reemplaza LOGO_SRC por ese data-uri.
 */
const LOGO_SRC = "https://www.texcumar.com/wp-content/uploads/2021/06/logo-texcumar.png"; // fallback público
const BRAND = {
  name: "TEXCUMAR S.A.",
  subtitle: "Portal de Verificación de Guías de Remisión",
  website: "www.texcumar.com",
};

const COLORS = {
  primary: "#162660",
  accent: "#F0B500",
  background: "#F4F7FB",
  card: "#FFFFFF",
  wave: "rgba(107,127,163,0.10)",
  textPrimary: "#162660",
  textSecondary: "#6B7FAA",
  border: "#E2E8F0",
  success: "#15803D",
  error: "#DC2626",
  shadow: "rgba(22,38,96,0.10)",
};

const clamp = (n, a, b) => Math.max(a, Math.min(b, n));

const normalizeInput = (raw) => {
  const s = (raw || "").trim();
  // Permite: "58498" o "001-001-000058498"
  return s.replace(/\s+/g, "");
};

const safeText = (v) => (v === null || v === undefined || v === "" ? "—" : String(v));

const usePrefersReducedMotion = () => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    setReduced(mq.matches);
    const handler = () => setReduced(mq.matches);
    mq.addEventListener?.("change", handler);
    return () => mq.removeEventListener?.("change", handler);
  }, []);
  return reduced;
};

const Icon = ({ name = "info", size = 18, color = "currentColor", style = {} }) => {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    xmlns: "http://www.w3.org/2000/svg",
    style: { display: "inline-block", verticalAlign: "middle", ...style },
  };
  const stroke = {
    stroke: color,
    strokeWidth: 2,
    strokeLinecap: "round",
    strokeLinejoin: "round",
  };

  const paths = {
    search: (
      <>
        <circle cx="11" cy="11" r="7" {...stroke} />
        <path d="M20 20l-3.5-3.5" {...stroke} />
      </>
    ),
    shield: (
      <>
        <path d="M12 2l7 4v6c0 5-3 9-7 10C8 21 5 17 5 12V6l7-4z" {...stroke} />
        <path d="M9 12l2 2 4-4" {...stroke} />
      </>
    ),
    calendar: (
      <>
        <path d="M7 3v2M17 3v2" {...stroke} />
        <path d="M4 8h16" {...stroke} />
        <rect x="4" y="5" width="16" height="16" rx="2" {...stroke} />
      </>
    ),
    user: (
      <>
        <path d="M20 21a8 8 0 0 0-16 0" {...stroke} />
        <circle cx="12" cy="7" r="4" {...stroke} />
      </>
    ),
    truck: (
      <>
        <path d="M3 7h11v10H3z" {...stroke} />
        <path d="M14 10h4l3 3v4h-7" {...stroke} />
        <circle cx="7" cy="18" r="2" {...stroke} />
        <circle cx="18" cy="18" r="2" {...stroke} />
      </>
    ),
    box: (
      <>
        <path d="M21 8l-9-5-9 5 9 5 9-5z" {...stroke} />
        <path d="M3 8v8l9 5 9-5V8" {...stroke} />
        <path d="M12 13v8" {...stroke} />
      </>
    ),
    warning: (
      <>
        <path d="M12 9v4" {...stroke} />
        <path d="M12 17h.01" {...stroke} />
        <path d="M10.3 4.5L2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.5a2 2 0 0 0-3.4 0z" {...stroke} />
      </>
    ),
    copy: (
      <>
        <rect x="9" y="9" width="11" height="11" rx="2" {...stroke} />
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" {...stroke} />
      </>
    ),
    spinner: (
      <>
        <path d="M12 2a10 10 0 1 0 10 10" {...stroke} />
      </>
    ),
    info: (
      <>
        <circle cx="12" cy="12" r="10" {...stroke} />
        <path d="M12 16v-5" {...stroke} />
        <path d="M12 8h.01" {...stroke} />
      </>
    ),
  };

  return <svg {...common}>{paths[name] || paths.info}</svg>;
};

const GlobalStyles = () => (
  <style>{`
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Noto Sans", "Helvetica Neue"; background: ${COLORS.background}; color: ${COLORS.textPrimary}; }
    a { color: inherit; text-decoration: none; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
  `}</style>
);

const Skeleton = ({ h = 12, w = "100%" }) => (
  <div
    style={{
      height: h,
      width: w,
      borderRadius: 10,
      background:
        "linear-gradient(90deg, rgba(107,127,163,0.10), rgba(107,127,163,0.18), rgba(107,127,163,0.10))",
      backgroundSize: "200% 100%",
      animation: "shimmer 1.2s ease-in-out infinite",
    }}
  />
);

const Toast = ({ message, tone = "info", onClose }) => {
  const bg =
    tone === "success"
      ? "rgba(21,128,61,0.10)"
      : tone === "error"
      ? "rgba(220,38,38,0.10)"
      : "rgba(240,181,0,0.14)";

  const border =
    tone === "success"
      ? "rgba(21,128,61,0.30)"
      : tone === "error"
      ? "rgba(220,38,38,0.30)"
      : "rgba(240,181,0,0.35)";

  const icon =
    tone === "success" ? "shield" : tone === "error" ? "warning" : "info";

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "fixed",
        right: 18,
        bottom: 18,
        zIndex: 9999,
        maxWidth: 420,
        background: "#fff",
        border: `1px solid ${border}`,
        boxShadow: "0 16px 40px rgba(22,38,96,0.14)",
        borderRadius: 14,
        padding: "12px 14px",
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
      }}
    >
      <div
        style={{
          width: 34,
          height: 34,
          borderRadius: 10,
          background: bg,
          display: "grid",
          placeItems: "center",
          flex: "0 0 auto",
        }}
      >
        <Icon name={icon} size={18} color={COLORS.primary} />
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 2 }}>
          {tone === "success"
            ? "Listo"
            : tone === "error"
            ? "Atención"
            : "Información"}
        </div>
        <div style={{ color: COLORS.textSecondary, fontSize: 13, lineHeight: 1.35 }}>
          {message}
        </div>
      </div>

      <button
        onClick={onClose}
        aria-label="Cerrar"
        style={{
          border: "none",
          background: "transparent",
          color: COLORS.textSecondary,
          cursor: "pointer",
          padding: 6,
          borderRadius: 10,
        }}
      >
        ✕
      </button>
    </div>
  );
};

const Section = ({ icon, title, children }) => (
  <section style={{ marginTop: 14 }}>
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "10px 12px",
        borderRadius: 14,
        background: "rgba(22,38,96,0.04)",
        border: `1px solid ${COLORS.border}`,
      }}
    >
      <div
        style={{
          width: 34,
          height: 34,
          borderRadius: 12,
          background: "rgba(240,181,0,0.18)",
          display: "grid",
          placeItems: "center",
        }}
      >
        <Icon name={icon} size={18} color={COLORS.primary} />
      </div>
      <div style={{ fontWeight: 800, color: COLORS.primary, letterSpacing: 0.2 }}>
        {title}
      </div>
    </div>
    <div style={{ marginTop: 10 }}>{children}</div>
  </section>
);

const FieldRow = ({ label, value }) => (
  <div
    style={{
      display: "grid",
      gridTemplateColumns: "180px 1fr",
      gap: 10,
      padding: "10px 12px",
      borderRadius: 12,
      border: `1px solid ${COLORS.border}`,
      background: "#fff",
    }}
  >
    <div style={{ color: COLORS.textSecondary, fontSize: 13, fontWeight: 700 }}>
      {label}
    </div>
    <div style={{ color: COLORS.textPrimary, fontSize: 14, fontWeight: 650 }}>
      {safeText(value)}
    </div>
  </div>
);

function TexcumarVerifica() {
  const reducedMotion = usePrefersReducedMotion();
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("idle"); // idle | loading | found | not_found | error
  const [guia, setGuia] = useState(null);
  const [lastQuery, setLastQuery] = useState("");
  const [toast, setToast] = useState(null);

  const inputRef = useRef(null);

  const styles = useMemo(() => {
    const t = (reducedMotion) => (reducedMotion ? "none" : "transform 150ms ease, box-shadow 200ms ease");
    return {
      page: {
        minHeight: "100vh",
        background: COLORS.background,
      },
      header: {
        background: COLORS.primary,
        color: "#fff",
        padding: "26px 18px 40px",
        position: "relative",
        overflow: "hidden",
        borderBottom: `6px solid ${COLORS.accent}`,
      },
      wave: {
        position: "absolute",
        inset: 0,
        background:
          `radial-gradient(1200px 180px at 10% 100%, ${COLORS.wave}, transparent 60%),` +
          `radial-gradient(900px 150px at 60% 115%, ${COLORS.wave}, transparent 55%),` +
          `radial-gradient(700px 140px at 95% 105%, ${COLORS.wave}, transparent 60%)`,
        pointerEvents: "none",
      },
      container: {
        maxWidth: 1040,
        margin: "0 auto",
      },
      brandRow: {
        display: "flex",
        alignItems: "center",
        gap: 14,
        flexWrap: "wrap",
      },
      logo: {
        height: 36,
        width: "auto",
        filter: "brightness(0) invert(1)",
        opacity: 0.95,
      },
      titleWrap: { display: "flex", flexDirection: "column", gap: 2 },
      brandName: {
        fontWeight: 900,
        letterSpacing: 1.2,
        fontSize: 14,
        opacity: 0.95,
      },
      brandSubtitle: {
        fontWeight: 600,
        fontSize: 13,
        color: "rgba(255,255,255,0.82)",
      },
      card: {
        maxWidth: 1040,
        margin: "-28px auto 0",
        background: "#fff",
        border: `1px solid ${COLORS.border}`,
        borderRadius: 18,
        boxShadow: `0 16px 44px ${COLORS.shadow}`,
        padding: 18,
      },
      hero: { display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" },
      heroLeft: { flex: "1 1 560px", minWidth: 280 },
      heroRight: { flex: "0 0 320px", minWidth: 260 },
      h1: {
        margin: 0,
        fontSize: 26,
        color: COLORS.primary,
        letterSpacing: 0.2,
      },
      p: { margin: "6px 0 0", color: COLORS.textSecondary, lineHeight: 1.5 },
      searchBox: {
        marginTop: 14,
        display: "flex",
        gap: 10,
        flexWrap: "wrap",
      },
      inputWrap: { flex: "1 1 360px", minWidth: 240, position: "relative" },
      input: (hasError) => ({
        width: "100%",
        padding: "12px 14px 12px 42px",
        borderRadius: 12,
        border: `1px solid ${hasError ? COLORS.error : COLORS.border}`,
        background: "#fff",
        color: COLORS.textPrimary,
        fontSize: 15,
        outline: "none",
        transition: "box-shadow 160ms ease, border-color 160ms ease",
      }),
      inputIcon: {
        position: "absolute",
        left: 12,
        top: "50%",
        transform: "translateY(-50%)",
        color: COLORS.textSecondary,
      },
      button: {
        padding: "12px 16px",
        borderRadius: 12,
        border: `1px solid ${COLORS.primary}`,
        background: COLORS.primary,
        color: "#fff",
        fontWeight: 800,
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        transition: t(reducedMotion),
        boxShadow: `0 10px 24px ${COLORS.shadow}`,
      },
      buttonGhost: {
        padding: "10px 14px",
        borderRadius: 12,
        border: `1px solid ${COLORS.border}`,
        background: "#fff",
        color: COLORS.primary,
        fontWeight: 800,
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        transition: t(reducedMotion),
      },
      badge: {
        border: `1px solid rgba(240,181,0,0.45)`,
        background: "rgba(240,181,0,0.15)",
        color: COLORS.primary,
        borderRadius: 999,
        padding: "8px 10px",
        display: "inline-flex",
        gap: 8,
        alignItems: "center",
        fontWeight: 800,
        fontSize: 12,
      },
      noteCard: {
        borderRadius: 16,
        border: `1px solid ${COLORS.border}`,
        background: "linear-gradient(180deg, rgba(22,38,96,0.03), rgba(22,38,96,0.01))",
        padding: 14,
      },
      noteTitle: { fontWeight: 900, marginBottom: 6, color: COLORS.primary },
      noteText: { color: COLORS.textSecondary, fontSize: 13, lineHeight: 1.45 },
      resultCard: {
        marginTop: 16,
        borderRadius: 18,
        border: `1px solid ${COLORS.border}`,
        background: "#fff",
        padding: 16,
        boxShadow: `0 12px 32px rgba(22,38,96,0.10)`,
        transition: t(reducedMotion),
      },
      statusBar: (tone) => ({
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "10px 12px",
        borderRadius: 14,
        border:
          tone === "success"
            ? "1px solid rgba(21,128,61,0.30)"
            : tone === "error"
            ? "1px solid rgba(220,38,38,0.30)"
            : `1px solid ${COLORS.border}`,
        background:
          tone === "success"
            ? "rgba(21,128,61,0.10)"
            : tone === "error"
            ? "rgba(220,38,38,0.08)"
            : "rgba(22,38,96,0.03)",
      }),
      footer: {
        maxWidth: 1040,
        margin: "18px auto 28px",
        padding: "0 18px",
        display: "flex",
        justifyContent: "space-between",
        gap: 12,
        flexWrap: "wrap",
        color: COLORS.textSecondary,
        fontSize: 12,
      },
      link: {
        color: COLORS.primary,
        fontWeight: 800,
      },
    };
  }, [reducedMotion]);

  const setFocusRing = (el) => {
    if (!el) return;
    el.style.boxShadow = "0 0 0 4px rgba(240,181,0,0.22)";
    el.style.borderColor = COLORS.accent;
  };

  const clearFocusRing = (el) => {
    if (!el) return;
    el.style.boxShadow = "none";
    el.style.borderColor = COLORS.border;
  };

  const copyToClipboard = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      setToast({ tone: "success", message: "Copiado al portapapeles." });
    } catch {
      setToast({ tone: "error", message: "No se pudo copiar. Intenta manualmente." });
    }
  };

  const fetchGuia = async (raw) => {
    const numero = normalizeInput(raw);
    if (!numero) {
      setToast({ tone: "error", message: "Ingresa un número de guía para verificar." });
      inputRef.current?.focus?.();
      return;
    }

    setLastQuery(numero);
    setStatus("loading");
    setGuia(null);

    try {
      const url = `${API_URL}?numero=${encodeURIComponent(numero)}`;
      const res = await fetch(url, { method: "GET" });
      const data = await res.json();

      if (data?.guia) {
        setGuia(data.guia);
        setStatus("found");
        setToast({ tone: "success", message: "Guía verificada correctamente." });
      } else if (data?.error === "not_found") {
        setStatus("not_found");
        setToast({
          tone: "error",
          message:
            "No se encontró la guía en el sistema. Posible falsificación o número incorrecto.",
        });
      } else {
        setStatus("error");
        setToast({
          tone: "error",
          message: "Respuesta inesperada del servicio. Intenta nuevamente.",
        });
      }
    } catch (e) {
      setStatus("error");
      setToast({
        tone: "error",
        message: "No se pudo conectar al servicio. Revisa tu conexión e intenta otra vez.",
      });
    }
  };

  const onSubmit = (e) => {
    e.preventDefault();
    fetchGuia(input);
  };

  const tone = status === "found" ? "success" : status === "not_found" || status === "error" ? "error" : "info";

  return (
    <div style={styles.page}>
      <GlobalStyles />

      <header style={styles.header}>
        <div style={styles.wave} />
        <div style={styles.container}>
          <div style={styles.brandRow}>
            <img src={LOGO_SRC} alt="Texcumar" style={styles.logo} />
            <div style={styles.titleWrap}>
              <div style={styles.brandName}>{BRAND.name}</div>
              <div style={styles.brandSubtitle}>{BRAND.subtitle}</div>
            </div>

            <div style={{ marginLeft: "auto" }}>
              <span style={styles.badge}>
                <Icon name="shield" size={16} color={COLORS.primary} />
                Portal oficial
              </span>
            </div>
          </div>
