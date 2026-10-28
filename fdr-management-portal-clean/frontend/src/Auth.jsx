import React, { useEffect, useState } from "react";
import axios from "axios";
import { Eye, EyeOff, GraduationCap, LogIn, ShieldCheck } from "lucide-react";
import { API_URL, errorMessage } from "./FdrDetails";

export default function Auth({ children }) {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [role, setRole] = useState("college");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  useEffect(() => {
    let active = true;
    const interceptor = axios.interceptors.response.use((response) => response, (failure) => {
      if (active && failure.response?.status === 401 && !failure.config?.url?.includes("/auth/")) {
        setUser(null);
        setPassword("");
        setError("Your session has expired. Please sign in again.");
      }
      return Promise.reject(failure);
    });
    axios.get(`${API_URL}/auth/me`).then((response) => { if (active) setUser(response.data); })
      .catch((failure) => { if (active && failure.response?.status !== 401) setError(errorMessage(failure)); })
      .finally(() => { if (active) setChecking(false); });
    return () => { active = false; axios.interceptors.response.eject(interceptor); };
  }, []);

  async function signIn(event) {
    event.preventDefault();
    setWorking(true);
    setError("");
    try {
      const response = await axios.post(`${API_URL}/auth/login`, { username: username.trim(), password, role });
      setUser(response.data);
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setPassword(""); setShowPassword(false); setWorking(false); }
  }

  async function signOut() {
    setWorking(true);
    setError("");
    try {
      await axios.post(`${API_URL}/auth/logout`);
      setUser(null);
      setUsername("");
      setPassword("");
    } catch (failure) {
      if (failure.response?.status === 401) setUser(null);
      else setError(errorMessage(failure));
    } finally { setWorking(false); }
  }

  function chooseRole(nextRole) {
    setRole(nextRole);
    setUsername("");
    setPassword("");
    setShowPassword(false);
    setError("");
  }

  if (checking) return <main className="session-check" role="status">Checking session...</main>;
  if (user) return <>{error && <div className="message-banner error-banner" role="alert">{error}</div>}{children(user, signOut, working)}</>;

  return <main className="login-page">
    <header className="login-brand"><ShieldCheck size={30} aria-hidden="true" /><div><h1>FDR Management Portal</h1><p>Security Deposit & Fixed Deposit Receipts</p></div></header>
    <section className="login-form-section" aria-labelledby="login-title">
      <div className="login-role-options" role="group" aria-label="Account type">
        <button type="button" className={role === "college" ? "selected-role" : ""} aria-pressed={role === "college"} disabled={working} onClick={() => chooseRole("college")}><GraduationCap size={19} aria-hidden="true" />College</button>
        <button type="button" className={role === "officer" ? "selected-role" : ""} aria-pressed={role === "officer"} disabled={working} onClick={() => chooseRole("officer")}><ShieldCheck size={19} aria-hidden="true" />Officer</button>
      </div>
      <h2 id="login-title">{role === "college" ? "College" : "Officer"} Login</h2>
      {error && <p className="login-error" id="login-error" role="alert">{error}</p>}
      <form onSubmit={signIn} aria-describedby={error ? "login-error" : undefined}><fieldset disabled={working}>
        <label htmlFor="login-username">Username</label>
        <input id="login-username" name="username" autoComplete="username" value={username} maxLength={100} required onChange={(event) => setUsername(event.target.value)} />
        <label htmlFor="login-password">Password</label>
        <div className="password-field"><input id="login-password" name="password" type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} maxLength={200} required onChange={(event) => setPassword(event.target.value)} />
          <button type="button" className="password-toggle" title={showPassword ? "Hide password" : "Show password"} aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeOff size={19} /> : <Eye size={19} />}</button>
        </div>
        <button type="submit" className="login-submit"><LogIn size={18} aria-hidden="true" />{working ? "Signing in..." : `Sign In as ${role === "college" ? "College" : "Officer"}`}</button>
      </fieldset></form>
    </section>
    <footer className="login-footer">FDR Management Portal | Local Prototype</footer>
  </main>;
}