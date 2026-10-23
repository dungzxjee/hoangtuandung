import { FormEvent, useEffect, useMemo, useState } from "react";
import { Activity, BarChart3, CalendarDays, CheckSquare, ContactRound, Mail, MessageSquare, PhoneCall, LogOut, Plus, Search, Users, X } from "lucide-react";

type User = { id: number; email: string; full_name: string; role: "admin" | "employee" };
type RecordData = Record<string, string | number | boolean | null>;
type Resource = "customers" | "leads" | "interactions" | "tasks" | "appointments";
type Screen = "app" | "login" | "register" | "forgot-password" | "reset-password";
type Field = { key: string; label: string; type?: string; options?: string[]; required?: boolean };

function currentScreen(): Screen {
  if (window.location.pathname === "/register") return "register";
  if (window.location.pathname === "/forgot-password") return "forgot-password";
  if (window.location.pathname === "/reset-password" || new URLSearchParams(window.location.hash.slice(1)).has("reset_token")) return "reset-password";
  if (window.location.pathname === "/login") return "login";
  return "app";
}

function screenPath(screen: Screen) {
  return screen === "app" ? "/" : `/${screen}`;
}

const resources: Record<Resource, { title: string; icon: typeof Users; fields: Field[]; columns: string[] }> = {
  customers: { title: "Khách hàng", icon: ContactRound, fields: [
    { key: "name", label: "Họ tên", required: true }, { key: "email", label: "Email", type: "email" },
    { key: "phone", label: "Điện thoại" }, { key: "company", label: "Công ty" },
    { key: "address", label: "Địa chỉ" }, { key: "notes", label: "Ghi chú", type: "textarea" },
  ], columns: ["name", "email", "phone", "company"] },
  leads: { title: "Khách hàng tiềm năng", icon: Users, fields: [
    { key: "name", label: "Họ tên", required: true }, { key: "email", label: "Email", type: "email" },
    { key: "phone", label: "Điện thoại" }, { key: "source", label: "Nguồn" },
    { key: "status", label: "Trạng thái", type: "select", options: ["new", "contacted", "qualified", "converted", "lost"] },
    { key: "notes", label: "Ghi chú", type: "textarea" },
  ], columns: ["name", "email", "phone", "source", "status"] },
  interactions: { title: "Tương tác", icon: ContactRound, fields: [
    { key: "customer_id", label: "Khách hàng", type: "customer", required: true },
    { key: "kind", label: "Loại", type: "select", options: ["call", "email", "meeting", "note"], required: true },
    { key: "occurred_at", label: "Thời gian (để trống = hiện tại)", type: "datetime-local" },
    { key: "notes", label: "Nội dung", type: "textarea", required: true },
  ], columns: ["customer_id", "kind", "occurred_at", "notes"] },
  tasks: { title: "Công việc", icon: CheckSquare, fields: [
    { key: "title", label: "Tiêu đề", required: true }, { key: "customer_id", label: "Khách hàng", type: "customer" },
    { key: "due_date", label: "Hạn hoàn thành", type: "date" },
    { key: "priority", label: "Ưu tiên", type: "select", options: ["low", "normal", "high", "urgent"] },
    { key: "status", label: "Trạng thái", type: "select", options: ["open", "in_progress", "done", "cancelled"] },
    { key: "description", label: "Mô tả", type: "textarea" },
  ], columns: ["title", "customer_id", "due_date", "priority", "status"] },
  appointments: { title: "Lịch hẹn", icon: CalendarDays, fields: [
    { key: "title", label: "Tiêu đề", required: true }, { key: "customer_id", label: "Khách hàng", type: "customer", required: true },
    { key: "starts_at", label: "Bắt đầu", type: "datetime-local", required: true },
    { key: "reminder_at", label: "Nhắc lúc", type: "datetime-local" },
    { key: "location", label: "Địa điểm" },
    { key: "status", label: "Trạng thái", type: "select", options: ["scheduled", "completed", "cancelled"] },
    { key: "notes", label: "Ghi chú", type: "textarea" },
  ], columns: ["title", "customer_id", "starts_at", "location", "status"] },
};

async function request<T>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...options.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Không thể kết nối máy chủ" }));
    if (response.status === 401) window.dispatchEvent(new Event("crm:unauthorized"));
    throw new Error(body.detail ?? `Lỗi HTTP ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export default function App() {
  const [screen, setScreen] = useState<Screen>(currentScreen);
  const [token, setToken] = useState(() => ["forgot-password", "reset-password"].includes(currentScreen())
    ? ""
    : localStorage.getItem("crm_token") ?? sessionStorage.getItem("crm_token") ?? "");
  const [rememberMe, setRememberMe] = useState(() => localStorage.getItem("crm_token") !== null);
  const [user, setUser] = useState<User | null>(null);
  const [active, setActive] = useState<Resource | "dashboard">("dashboard");
  const [records, setRecords] = useState<RecordData[]>([]);
  const [customerOptions, setCustomerOptions] = useState<RecordData[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [reminders, setReminders] = useState<{ tasks: RecordData[]; appointments: RecordData[] }>({ tasks: [], appointments: [] });
  const [recentInteractions, setRecentInteractions] = useState<RecordData[]>([]);
  const [interactionTotal, setInteractionTotal] = useState(0);
  const [interactionTrend, setInteractionTrend] = useState<{ label: string; count: number }[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<{ resource: Resource; record?: RecordData } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RecordData | null>(null);
  const [userNotice, setUserNotice] = useState("");
  const [loginNotice, setLoginNotice] = useState("");
  const [resetToken, setResetToken] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("reset_token"));

  function navigateTo(nextScreen: Screen, replace = false) {
    const nextUrl = screenPath(nextScreen) + (nextScreen === "reset-password" ? window.location.hash : "");
    window.history[replace ? "replaceState" : "pushState"]({}, "", nextUrl);
    setScreen(nextScreen);
    setResetToken(nextScreen === "reset-password"
      ? new URLSearchParams(window.location.hash.slice(1)).get("reset_token")
      : null);
    setError("");
    setLoginNotice("");
  }

  useEffect(() => {
    function handleUnauthorized() {
      localStorage.removeItem("crm_token");
      sessionStorage.removeItem("crm_token");
      setRememberMe(false);
      setToken("");
      setUser(null);
      setRecords([]);
      window.history.replaceState({}, "", "/login");
      setScreen("login");
      setError("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.");
    }
    window.addEventListener("crm:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("crm:unauthorized", handleUnauthorized);
  }, []);

  useEffect(() => {
    if (!resetToken) return;
    localStorage.removeItem("crm_token");
    sessionStorage.removeItem("crm_token");
    setRememberMe(false);
    setToken("");
    setUser(null);
  }, [resetToken]);

  useEffect(() => {
    function syncResetToken() {
      setResetToken(new URLSearchParams(window.location.hash.slice(1)).get("reset_token"));
      setScreen(currentScreen());
    }
    window.addEventListener("hashchange", syncResetToken);
    window.addEventListener("popstate", syncResetToken);
    return () => {
      window.removeEventListener("hashchange", syncResetToken);
      window.removeEventListener("popstate", syncResetToken);
    };
  }, []);

  useEffect(() => {
    if (!token) { setUser(null); return; }
    request<User>("/auth/me", token).then(setUser).catch(() => logout());
  }, [token]);

  useEffect(() => {
    if (token && user && screen === "login") navigateTo("app", true);
  }, [screen, token, user]);

  useEffect(() => {
    if (!token || !user || screen !== "app") return;
    setError("");
    if (active === "dashboard") {
      Promise.all([
        request<Record<string, number>>("/dashboard", token),
        request<{ tasks: RecordData[]; appointments: RecordData[] }>("/reminders", token),
        request<RecordData[]>("/interactions", token),
        request<RecordData[]>("/customers", token),
      ]).then(([dashboard, due, interactions, customers]) => {
        const customerNames = new Map(customers.map((customer) => [String(customer.id), String(customer.name)]));
        setStats(dashboard);
        setReminders(due);
        setInteractionTotal(interactions.length);
        setRecentInteractions(interactions.slice(0, 5).map((interaction) => ({
          ...interaction,
          customer_name: customerNames.get(String(interaction.customer_id)) ?? `Khách hàng #${interaction.customer_id}`,
        })));
        setInteractionTrend(buildInteractionTrend(interactions));
      }).catch(showError);
    } else {
      const path = `/${active}${query ? `?q=${encodeURIComponent(query)}` : ""}`;
      request<RecordData[]>(path, token).then(setRecords).catch(showError);
    }
  }, [active, query, screen, token, user]);

  function showError(reason: unknown) { setError(reason instanceof Error ? reason.message : "Đã xảy ra lỗi"); }
  function logout() {
    localStorage.removeItem("crm_token"); sessionStorage.removeItem("crm_token");
    setRememberMe(false); setToken(""); setUser(null); setRecords([]);
    navigateTo("login", true);
  }

  async function openRecordModal(resource: Resource, record?: RecordData) {
    if (resource === "interactions" || resource === "tasks" || resource === "appointments") {
      if (!token) return;
      setBusy(true);
      try {
        setCustomerOptions(await request<RecordData[]>("/customers", token));
        setModal({ resource, record });
      } catch (reason) { showError(reason); } finally { setBusy(false); }
      return;
    }
    setModal({ resource, record });
  }

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const result = await fetch("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: data.get("email"),
          password: data.get("password"),
          remember_me: rememberMe,
        }),
      });
      const body = await result.json();
      if (!result.ok) throw new Error(body.detail ?? "Đăng nhập thất bại");
      if (screen === "register" && body.user.role !== "admin") {
        throw new Error("Chỉ Admin mới được phép tạo tài khoản.");
      }
      if (rememberMe) {
        localStorage.setItem("crm_token", body.access_token);
        sessionStorage.removeItem("crm_token");
      } else {
        sessionStorage.setItem("crm_token", body.access_token);
        localStorage.removeItem("crm_token");
      }
      form.reset();
      setToken(body.access_token); setUser(body.user);
      if (screen !== "register") navigateTo("app", true);
    } catch (reason) { showError(reason); } finally { setBusy(false); }
  }

  async function requestPasswordReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setLoginNotice("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/auth/forgot-password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: data.get("email") }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Không thể gửi yêu cầu đặt lại mật khẩu");
      setLoginNotice(body.message);
    } catch (reason) { showError(reason); } finally { setBusy(false); }
  }

  async function resetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password") ?? "");
    if (password !== String(data.get("confirm_password") ?? "")) {
      setError("Mật khẩu xác nhận không trùng khớp.");
      return;
    }
    if (!resetToken) { setError("Liên kết đặt lại mật khẩu không hợp lệ."); return; }
    setBusy(true); setError(""); setLoginNotice("");
    try {
      const response = await fetch("/api/auth/reset-password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: resetToken, password }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Không thể đặt lại mật khẩu");
      setResetToken(null);
      navigateTo("login", true);
      setLoginNotice(body.message);
    } catch (reason) { showError(reason); } finally { setBusy(false); }
  }

  async function saveRecord(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!modal || !token) return;
    setBusy(true); setError("");
    const formData = new FormData(event.currentTarget);
    const values: Record<string, string | number | null> = {};
    for (const field of resources[modal.resource].fields) {
      const value = String(formData.get(field.key) ?? "").trim();
      if (value !== "") {
        values[field.key] = field.type === "number" || field.type === "customer" ? Number(value)
          : field.type === "datetime-local" ? new Date(value).toISOString()
          : value;
      }
    }
    try {
      const id = modal.record?.id;
      await request(`/${modal.resource}${id ? `/${id}` : ""}`, token, {
        method: id ? "PUT" : "POST", body: JSON.stringify(values),
      });
      setModal(null);
      const updated = await request<RecordData[]>(`/${modal.resource}`, token);
      setRecords(updated);
    } catch (reason) { showError(reason); } finally { setBusy(false); }
  }

  async function deleteRecord(record: RecordData) {
    if (!token || active === "dashboard") return;
    setDeleteTarget(record);
  }

  async function confirmDelete() {
    if (!token || !deleteTarget || active === "dashboard") return;
    try {
      await request(`/${active}/${deleteTarget.id}`, token, { method: "DELETE" });
      setRecords((items) => items.filter((item) => item.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (reason) { showError(reason); }
  }

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    if (values.get("password") !== values.get("confirm_password")) {
      setError("Mật khẩu xác nhận không trùng khớp.");
      return;
    }
    setBusy(true); setError(""); setLoginNotice("");
    try {
      const response = await fetch("/api/auth/register", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          full_name: values.get("full_name"),
          email: values.get("email"),
          password: values.get("password"),
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Không thể tạo tài khoản");
      form.reset();
      navigateTo("login");
      setLoginNotice("Tạo tài khoản thành công. Bạn có thể đăng nhập ngay.");
    } catch (reason) { showError(reason); } finally { setBusy(false); }
  }

  async function convertLead(record: RecordData) {
    if (!token) return;
    try {
      await request(`/leads/${record.id}/convert`, token, { method: "POST" });
      setRecords(await request<RecordData[]>("/leads", token));
      setUserNotice("Đã chuyển đổi khách hàng tiềm năng thành khách hàng.");
    } catch (reason) { showError(reason); }
  }

  const resource = active !== "dashboard" ? resources[active] : null;
  const visibleRecords = useMemo(() => records, [records]);
  const maxInteractionCount = Math.max(1, ...interactionTrend.map((month) => month.count));
  const interactionCount = interactionTotal;
  if (screen === "forgot-password") return <main className="login-wrap"><div className="login-shell">
    <form className="login-card" onSubmit={requestPasswordReset}>
      <div className="brand-mark">C</div><p className="eyebrow">HỖ TRỢ TÀI KHOẢN</p>
      <h1>Quên mật khẩu?</h1><p className="muted">Nhập email đã đăng ký. Nếu tài khoản tồn tại, liên kết khôi phục sẽ được gửi đến hộp thư.</p>
      {error && <div className="error" role="alert">{error}</div>}
      {loginNotice && <div className="success" role="status">{loginNotice}</div>}
      <label>Email<input name="email" type="email" required autoComplete="email" /></label>
      <button className="primary full" disabled={busy}>{busy ? "Đang gửi..." : "Gửi liên kết đặt lại"}</button>
      <button type="button" className="login-back" onClick={() => navigateTo("login")}>Quay lại đăng nhập</button>
    </form>
  </div></main>;

  if (screen === "reset-password") return <main className="login-wrap"><div className="login-shell">
    <form className="login-card" onSubmit={resetPassword}>
      <div className="brand-mark">C</div><p className="eyebrow">BẢO MẬT TÀI KHOẢN</p>
      <h1>Đặt lại mật khẩu</h1><p className="muted">Tạo mật khẩu mới có ít nhất 12 ký tự, gồm chữ hoa, chữ thường và chữ số.</p>
      {error && <div className="error" role="alert">{error}</div>}
      <label>Mật khẩu mới<input name="password" type="password" required minLength={12} maxLength={128} autoComplete="new-password" /></label>
      <label>Xác nhận mật khẩu<input name="confirm_password" type="password" required minLength={12} maxLength={128} autoComplete="new-password" /></label>
      <button className="primary full" disabled={busy}>{busy ? "Đang cập nhật..." : "Xác nhận đặt lại"}</button>
      <button type="button" className="login-back" onClick={() => navigateTo("login")}>Quay lại đăng nhập</button>
    </form>
  </div></main>;

  if (screen === "register") {
    return <main className="login-wrap"><div className="login-shell register-shell">
      <form className="login-card register-card" onSubmit={createAccount}>
        <div className="brand-mark">C</div><p className="eyebrow">THAM GIA CRM</p>
        <h1>Tạo tài khoản</h1><p className="muted">Tài khoản đăng ký mới có quyền User.</p>
        {error && <div className="error" role="alert">{error}</div>}
        <label>Họ tên<input name="full_name" required maxLength={150} /></label>
        <label>Email đăng nhập<input name="email" type="email" required autoComplete="username" /></label>
        <label>Mật khẩu (12+ ký tự, hoa/thường/số)<input name="password" type="password" required minLength={12} autoComplete="new-password" /></label>
        <label>Xác nhận mật khẩu<input name="confirm_password" type="password" required minLength={12} autoComplete="new-password" /></label>
        <button className="primary full" disabled={busy}>{busy ? "Đang tạo..." : "Tạo tài khoản"}</button>
        <button type="button" className="login-back" onClick={() => navigateTo("login")}>Quay lại đăng nhập</button>
      </form>
    </div></main>;
  }

  if (screen === "login" || !token || !user) return <main className="login-wrap"><div className="login-shell">
    <form className="login-card" onSubmit={signIn}>
      <div className="brand-mark">C</div><p className="eyebrow">CHĂM SÓC KHÁCH HÀNG</p>
      <h1>Đăng nhập CRM</h1><p className="muted">Quản lý quan hệ khách hàng tập trung.</p>
      {error && <div className="error" role="alert">{error}</div>}
      {loginNotice && <div className="success" role="status">{loginNotice}</div>}
      <label>Email đăng nhập<input name="email" type="email" required autoComplete="username" /></label>
      <label>Mật khẩu<input name="password" type="password" required autoComplete="current-password" /></label>
      <label className="login-remember"><input type="checkbox" checked={rememberMe} onChange={(event) => setRememberMe(event.target.checked)} />Ghi nhớ đăng nhập trên thiết bị này (tối đa 30 ngày)</label>
      <button className="primary full" disabled={busy}>{busy ? "Đang đăng nhập..." : "Đăng nhập"}</button>
      <small>Tài khoản được quản trị viên cấp.</small>
    </form>
    <nav className="login-support" aria-label="Hỗ trợ đăng nhập">
      <button type="button" onClick={() => navigateTo("register")}>Tạo tài khoản</button>
      <button type="button" onClick={() => navigateTo("forgot-password")}>Quên mật khẩu?</button>
    </nav>
  </div></main>;

  const menu: Array<{ id: typeof active; label: string; icon: typeof Users }> = [
    { id: "dashboard", label: "Tổng quan", icon: BarChart3 },
    ...Object.entries(resources).map(([id, item]) => ({ id: id as Resource, label: item.title, icon: item.icon })),
  ];
  return <div className="layout">
    <aside className="sidebar"><div className="logo"><span className="brand-mark small">C</span><span>Care<span className="logo-light">CRM</span></span></div>
      <nav className="sidebar-navigation" aria-label="Điều hướng CRM">
        <div className="nav-group"><p className="nav-label">TỔNG QUAN</p>
          {menu.filter((item) => item.id === "dashboard").map((item) => <button key={item.id} className={active === item.id ? "nav-item selected" : "nav-item"} onClick={() => { setActive(item.id); setQuery(""); }}><item.icon size={18} />{item.label}</button>)}
        </div>
        <div className="nav-group"><p className="nav-label">NGHIỆP VỤ CRM</p>
          {menu.filter((item) => item.id !== "dashboard").map((item) => <button key={item.id} className={active === item.id ? "nav-item selected" : "nav-item"} onClick={() => { setActive(item.id); setQuery(""); }}><item.icon size={18} />{item.label}</button>)}
        </div>
      </nav>
    </aside>
    <main className="main"><header className="topbar"><div><span className="breadcrumb">KHÔNG GIAN LÀM VIỆC</span><strong>{active === "dashboard" ? "Tổng quan" : resource?.title}</strong></div><span className="status-pill"><i /> Hệ thống hoạt động</span><div className="topbar-user"><span className="avatar">{user.full_name.slice(0, 1)}</span><div className="topbar-user-label"><strong>{user.full_name}</strong><span>{user.role === "admin" ? "Quản trị viên" : "Nhân viên"}</span></div><button className="topbar-logout" aria-label="Đăng xuất" title="Đăng xuất" onClick={logout}><LogOut size={17} /></button></div></header>
      <section className="content">
        {error && <div className="error" role="alert">{error}<button aria-label="Đóng" onClick={() => setError("")}><X size={16} /></button></div>}
        {userNotice && <div className="success">{userNotice}</div>}
        {active === "dashboard" ? <><div className="page-heading"><div><p className="eyebrow">TỔNG QUAN</p><h1>Xin chào, {user.full_name.split(" ")[0]} 👋</h1><p className="muted">Theo dõi tình hình chăm sóc khách hàng của bạn.</p></div></div>
          <div className="stats-grid">{([
            ["Khách hàng", stats.customers, ContactRound, "customer", "Trong hệ thống"],
            ["Tiềm năng", stats.leads, Users, "lead", "Đang theo dõi"],
            ["Công việc mở", stats.open_tasks, CheckSquare, "task", "Cần xử lý"],
            ["Lịch hẹn", stats.appointments, CalendarDays, "appointment", "Đã ghi nhận"],
          ] as const).map(([label, value, Icon, key, caption]) => <article className={`stat-card stat-card--${key}`} key={key}><div className="stat-top"><span>{label}</span><Icon size={20} /></div><strong>{Number(value ?? 0).toLocaleString("vi-VN")}</strong><small>{caption}</small></article>)}</div>
          <section className="analytics-grid" aria-label="Phân tích và hoạt động CRM">
            <article className="analytics-card">
              <div className="analytics-heading"><div><p className="eyebrow">PHÂN TÍCH</p><h2>Tương tác theo tháng</h2></div><span>{interactionCount.toLocaleString("vi-VN")} lượt</span></div>
              {interactionTrend.some((month) => month.count > 0) ? <div className="interaction-chart" role="img" aria-label="Biểu đồ số lượt tương tác trong sáu tháng gần nhất">
                {interactionTrend.map((month) => <div className="chart-column" key={month.label}>
                  <strong>{month.count}</strong>
                  <div className="chart-bar-track"><span style={{ height: `${Math.max(4, month.count / maxInteractionCount * 100)}%` }} /></div>
                  <small>{month.label}</small>
                </div>)}
              </div> : <div className="chart-empty"><Activity size={20} /><span>Chưa có tương tác trong sáu tháng gần đây.</span></div>}
              <p className="analytics-footnote">Tính từ tối đa 500 tương tác gần nhất mà tài khoản được phép xem.</p>
            </article>
            <article className="analytics-card">
              <div className="analytics-heading"><div><p className="eyebrow">NHẬT KÝ CRM</p><h2>Hoạt động gần đây</h2></div><span>{recentInteractions.length} hoạt động</span></div>
              {recentInteractions.length ? <div className="activity-list">{recentInteractions.map((item) => {
                const Icon = interactionIcons[String(item.kind)] ?? Activity;
                return <div className="activity-row" key={String(item.id)}>
                  <span className="activity-icon"><Icon size={16} /></span>
                  <div className="activity-copy"><strong>{interactionKindLabel(String(item.kind))} · {String(item.customer_name)}</strong><p>{String(item.notes ?? "")}</p></div>
                  <time>{displayValue(item.occurred_at)}</time>
                </div>;
              })}</div> : <div className="chart-empty"><Activity size={20} /><span>Chưa có hoạt động tương tác nào.</span></div>}
            </article>
          </section>
          <div className="reminder-grid">
            <article className="reminder-card"><div className="reminder-heading"><CheckSquare size={18} /><h2>Việc đến hạn</h2><span>{reminders.tasks.length}</span></div>
              {reminders.tasks.length ? reminders.tasks.map((item) => <div className="reminder-row" key={String(item.id)}><strong>{String(item.title)}</strong><small>Hạn: {displayValue(item.due_date)}</small></div>) : <p className="muted">Không có công việc quá hạn.</p>}
            </article>
            <article className="reminder-card"><div className="reminder-heading"><CalendarDays size={18} /><h2>Lịch hẹn cần nhắc</h2><span>{reminders.appointments.length}</span></div>
              {reminders.appointments.length ? reminders.appointments.map((item) => <div className="reminder-row" key={String(item.id)}><strong>{String(item.title)}</strong><small>Bắt đầu: {displayValue(item.starts_at)}</small></div>) : <p className="muted">Không có lịch hẹn đến giờ nhắc.</p>}
            </article>
          </div>
          <div className="welcome-card"><div><p className="eyebrow">BẮT ĐẦU CÔNG VIỆC</p><h2>Mọi tương tác, một nơi quản lý.</h2><p>Thêm khách hàng, ghi nhận tương tác và theo dõi lịch chăm sóc tiếp theo.</p></div><button className="primary" onClick={() => { setActive("customers"); setModal({ resource: "customers" }); }}><Plus size={17} /> Thêm khách hàng</button></div>
        </> : <><div className="page-heading"><div><p className="eyebrow">QUẢN LÝ DỮ LIỆU</p><h1>{resource?.title}</h1><p className="muted">Tạo mới, tìm kiếm và cập nhật dữ liệu CRM.</p></div>
          {resource && <button className="primary" onClick={() => openRecordModal(active as Resource)} disabled={busy}><Plus size={17} /> Thêm mới</button>}
        </div>
        <div className="table-card"><div className="table-toolbar"><div className="search"><Search size={17} /><input placeholder="Tìm theo tên hoặc email..." value={query} onChange={(e) => setQuery(e.target.value)} /></div><span>{visibleRecords.length} bản ghi</span></div>
          <div className="table-scroll"><table><thead><tr>{resource?.columns.map((col) => <th key={col}>{columnLabel(col)}</th>)}<th>Thao tác</th></tr></thead>
            <tbody>{visibleRecords.map((record) => <tr key={String(record.id)}>{(resource?.columns ?? []).map((col) => <td key={col}>{renderTableCell(col, record[col], active, customerOptions)}</td>)}
              <td>{resource ? <div className="actions">{active === "leads" && record.status !== "converted" && <button className="text-button" onClick={() => convertLead(record)}>Chuyển đổi</button>}<button className="text-button" onClick={() => openRecordModal(active as Resource, record)} disabled={busy}>Sửa</button>{user.role === "admin" && <button className="text-button danger-text" onClick={() => deleteRecord(record)}>Xóa</button>}</div>
                : "—"}</td></tr>)}
              {visibleRecords.length === 0 && <tr><td colSpan={8} className="empty">Chưa có dữ liệu. Hãy tạo bản ghi đầu tiên.</td></tr>}</tbody>
          </table></div>
        </div></>}
      </section>
    </main>
    {modal && <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setModal(null); }}><form className="modal" onSubmit={saveRecord}>
      <div className="modal-head"><div><p className="eyebrow">CRM</p><h2>{modal.record ? "Cập nhật" : "Tạo mới"} {resources[modal.resource].title.toLowerCase()}</h2></div><button type="button" className="icon-button" aria-label="Đóng" onClick={() => setModal(null)}><X size={20} /></button></div>
      <div className="form-grid">{resources[modal.resource].fields.map((field) => <label key={field.key} className={field.type === "textarea" ? "wide" : ""}>{field.label}
        {field.type === "customer" ? <select name={field.key} required={field.required} defaultValue={String(modal.record?.[field.key] ?? "")} disabled={Boolean(field.required && customerOptions.length === 0)}><option value="">{customerOptions.length ? field.required ? "Chọn khách hàng" : "Không liên kết khách hàng" : "Chưa có khách hàng"}</option>{customerOptions.map((customer) => <option key={String(customer.id)} value={String(customer.id)}>{String(customer.name)} (#{String(customer.id)})</option>)}</select>
          : field.type === "select" ? <select name={field.key} defaultValue={String(modal.record?.[field.key] ?? field.options?.[0] ?? "")}>{field.options?.map((option) => <option key={option} value={option}>{option}</option>)}</select>
          : field.type === "textarea" ? <textarea name={field.key} rows={3} defaultValue={String(modal.record?.[field.key] ?? "")} required={field.required} />
          : <input name={field.key} type={field.type ?? "text"} required={field.required} defaultValue={field.type === "datetime-local" && modal.record?.[field.key] ? dateTimeForInput(String(modal.record[field.key])) : String(modal.record?.[field.key] ?? "")} />}</label>)}
      </div><div className="modal-actions"><button type="button" className="secondary" onClick={() => setModal(null)}>Hủy</button><button className="primary" disabled={busy || (modal.resource === "interactions" && customerOptions.length === 0)}>{busy ? "Đang lưu..." : "Lưu thay đổi"}</button></div>
    </form></div>}
      {deleteTarget && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setDeleteTarget(null); }}><section className="modal confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-title">
        <div className="modal-head"><div><p className="eyebrow">XÁC NHẬN</p><h2 id="delete-title">Xóa dữ liệu này?</h2></div><button className="icon-button" aria-label="Đóng" onClick={() => setDeleteTarget(null)}><X size={20} /></button></div>
        <p>Thao tác này không thể hoàn tác. Dữ liệu liên quan sẽ được bảo vệ theo ràng buộc hệ thống.</p>
        <div className="modal-actions"><button className="secondary" onClick={() => setDeleteTarget(null)}>Hủy</button><button className="danger-button" onClick={confirmDelete}>Xóa</button></div>
      </section></div>}
  </div>;
}

function columnLabel(key: string) {
  const labels: Record<string, string> = { name: "Họ tên", email: "Email", phone: "Điện thoại", company: "Công ty", source: "Nguồn", status: "Trạng thái", customer_id: "Khách hàng", kind: "Loại", occurred_at: "Thời gian", notes: "Nội dung", title: "Tiêu đề", due_date: "Hạn", priority: "Ưu tiên", starts_at: "Bắt đầu", location: "Địa điểm", full_name: "Họ tên", role: "Vai trò", is_active: "Hoạt động" };
  return labels[key] ?? key;
}

function renderTableCell(key: string, value: RecordData[string] | undefined, resource: Resource | "dashboard", customers: RecordData[]) {
  if (resource === "customers" && key === "name" && typeof value === "string") {
    return <span className="customer-cell"><span className="customer-avatar">{value.trim().slice(0, 1).toLocaleUpperCase("vi-VN")}</span>{value}</span>;
  }
  if (key === "customer_id" && value !== undefined && value !== null) {
    const customer = customers.find((item) => String(item.id) === String(value));
    return customer ? String(customer.name) : `Khách hàng #${String(value)}`;
  }
  if (typeof value === "string" && ["status", "priority", "kind"].includes(key)) {
    const labels: Record<string, string> = {
      new: "Mới", contacted: "Đã liên hệ", qualified: "Đủ điều kiện", converted: "Đã chuyển đổi", lost: "Đã đóng",
      open: "Chưa bắt đầu", in_progress: "Đang xử lý", done: "Hoàn thành", cancelled: "Đã hủy",
      scheduled: "Đã lên lịch", completed: "Hoàn thành", low: "Thấp", normal: "Bình thường", high: "Cao", urgent: "Khẩn cấp",
      call: "Gọi điện", email: "Email", meeting: "Cuộc hẹn", note: "Ghi chú",
    };
    return <span className={`badge badge--${value}`}>{labels[value] ?? value}</span>;
  }
  return displayValue(value);
}

const interactionIcons: Record<string, typeof Activity> = {
  call: PhoneCall,
  email: Mail,
  meeting: CalendarDays,
  note: MessageSquare,
};

function interactionKindLabel(kind: string) {
  const labels: Record<string, string> = { call: "Gọi điện", email: "Email", meeting: "Cuộc hẹn", note: "Ghi chú" };
  return labels[kind] ?? "Tương tác";
}

function buildInteractionTrend(interactions: RecordData[]) {
  const current = new Date();
  const months = Array.from({ length: 6 }, (_, index) => new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() - 5 + index, 1)));
  const counts = new Map(months.map((month) => [`${month.getUTCFullYear()}-${month.getUTCMonth()}`, 0]));
  for (const interaction of interactions) {
    if (typeof interaction.occurred_at !== "string") continue;
    const value = interaction.occurred_at;
    const date = new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
    if (Number.isNaN(date.getTime())) continue;
    const key = `${date.getUTCFullYear()}-${date.getUTCMonth()}`;
    if (counts.has(key)) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return months.map((month) => ({
    label: month.toLocaleDateString("vi-VN", { month: "short", timeZone: "UTC" }).replace(/\.$/, ""),
    count: counts.get(`${month.getUTCFullYear()}-${month.getUTCMonth()}`) ?? 0,
  }));
}

function displayValue(value: RecordData[string] | undefined) {
  if (typeof value === "boolean") return value ? "Có" : "Không";
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" && value.includes("T")) {
    const date = new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("vi-VN");
  }
  return String(value);
}

function dateTimeForInput(value: string) {
  const date = new Date(value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return "";
  const localDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return localDate.toISOString().slice(0, 16);
}
