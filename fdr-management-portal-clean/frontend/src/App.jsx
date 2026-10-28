
import React, { useEffect, useRef, useState } from "react";
import axios from "axios";
import { GraduationCap, LogOut, ShieldCheck } from "lucide-react";
import Auth from "./Auth";
import FdrDetails, { API_URL, RequestHistory, errorMessage } from "./FdrDetails";
import PortfolioIntelligence from "./PortfolioIntelligence";
import "./App.css";

function Dashboard({ user, onLogout, signingOut }) {
  const [fdrs, setFdrs] = useState([]);
  const [intelligence, setIntelligence] = useState(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const view = user.role;
  const [renewals, setRenewals] = useState([]);
  const [releases, setReleases] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [messageIsError, setMessageIsError] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const registration = useRef(null);
  const details = useRef(null);

  const [form, setForm] = useState({
    institution_name: "",
    certificate_number: "",
    bank_name: "",
    amount: "",
    maturity_date: "",
  });

  // Load FDR records and maturity alerts from FastAPI
  async function loadFdrs() {
    try {
      setLoading(true);

      const [fdrResponse, alertResponse, renewalResponse, releaseResponse] = await Promise.all([
        axios.get(`${API_URL}/fdrs`),
        axios.get(`${API_URL}/intelligence`),
        axios.get(`${API_URL}/renewal-requests`),
        axios.get(`${API_URL}/release-requests`),
      ]);

      setFdrs(fdrResponse.data);
      setIntelligence(alertResponse.data);
      setRenewals(renewalResponse.data);
      setReleases(releaseResponse.data);
      return true;
    } catch (error) {
      console.error("Error loading FDR data:", error);
      setMessage(`Could not load FDR data. ${errorMessage(error)}`);
      setMessageIsError(true);
      return false;
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadFdrs();
  }, []);

  async function perform(action, successMessage) {
    setBusy(true);
    setMessage("");
    setMessageIsError(false);
    try {
      await action();
      const refreshed = await loadFdrs();
      if (refreshed) setMessage(successMessage);
      return true;
    } catch (error) {
      setMessage(errorMessage(error));
      setMessageIsError(true);
      return false;
    } finally { setBusy(false); }
  }

  function selectFdr(id) {
    setSelectedId(id);
    requestAnimationFrame(() => details.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  function editFdr(fdr) {
    setEditingId(fdr.id);
    setForm({ institution_name: fdr.institution_name, certificate_number: fdr.certificate_number,
      bank_name: fdr.bank_name, amount: fdr.amount, maturity_date: fdr.maturity_date });
    registration.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function clearForm() {
    setEditingId(null);
    setForm({ institution_name: "", certificate_number: "", bank_name: "", amount: "", maturity_date: "" });
  }

  // Update form fields
  function handleChange(event) {
    const { name, value } = event.target;

    setForm((previousForm) => ({
      ...previousForm,
      [name]: value,
    }));
  }

  // Register a new FDR
  async function handleSubmit(event) {
    event.preventDefault();
    const success = await perform(() => axios({
      method: editingId ? "put" : "post",
      url: `${API_URL}/fdrs${editingId ? `/${editingId}` : ""}`,
      data: {
        institution_name: form.institution_name.trim(),
        certificate_number: form.certificate_number.trim(),
        bank_name: form.bank_name.trim(),
        amount: Number(form.amount),
        maturity_date: form.maturity_date,
      },
    }), editingId ? "FDR corrections saved. Submit it when ready." : "FDR registered successfully.");
    if (success) clearForm();
  }

  // Submit an FDR or perform an officer review action
  async function updateStatus(id, action) {
    await perform(() => action === "submit"
      ? axios.post(`${API_URL}/fdrs/${id}/submit`)
      : axios.post(`${API_URL}/fdrs/${id}/review`, { action }), "FDR status updated successfully.");
  }

  // Render actions based on the selected dashboard and current status
  function renderActions(fdr) {
    if (view === "college") {
      if (
        fdr.status === "Draft" ||
        fdr.status === "Correction Required"
      ) {
        return (
          <div className="action-group"><button disabled={busy || loading} onClick={() => editFdr(fdr)}>Edit</button><button
            className="action-button"
            disabled={busy || loading}
            onClick={() => updateStatus(fdr.id, "submit")}
          >
            Submit FDR
          </button></div>
        );
      }

      return null;
    }

    if (fdr.status === "Submitted") {
      return (
        <button
          className="action-button"
          disabled={busy || loading}
          onClick={() => updateStatus(fdr.id, "under_review")}
        >
          Start Review
        </button>
      );
    }

    if (fdr.status === "Under Review") {
      return (
        <button onClick={() => selectFdr(fdr.id)}>Review FDR</button>
      );
    }

    return null;
  }

  // Dashboard summary counts
  const stats = [
    {
      label: "Total FDRs",
      value: fdrs.length,
      className: "stat-total",
    },
    {
      label: "Draft",
      value: fdrs.filter((fdr) => fdr.status === "Draft").length,
      className: "stat-draft",
    },
    {
      label: "Submitted",
      value: fdrs.filter((fdr) => fdr.status === "Submitted").length,
      className: "stat-submitted",
    },
    {
      label: "Under Review",
      value: fdrs.filter((fdr) => fdr.status === "Under Review").length,
      className: "stat-reviewing",
    },
    {
      label: "Verified",
      value: fdrs.filter((fdr) => fdr.status === "Verified").length,
      className: "stat-verified",
    },
    {
      label: "Corrections",
      value: fdrs.filter(
        (fdr) => fdr.status === "Correction Required"
      ).length,
      className: "stat-corrections",
    },
  ];

  // Officer view only shows records requiring review
  const visibleFdrs =
    (view === "officer"
      ? fdrs.filter(
          (fdr) =>
            fdr.status === "Submitted" ||
            fdr.status === "Under Review"
        )
      : fdrs).filter((fdr) => (statusFilter === "All" || fdr.status === statusFilter) &&
        [fdr.institution_name, fdr.certificate_number, fdr.bank_name].some((value) => value.toLowerCase().includes(search.toLowerCase().trim())));
      const selectedFdr = fdrs.find((fdr) => fdr.id === selectedId);
      const pendingRequest = [...renewals, ...releases].some((request) => request.fdr_id === selectedId && ["Pending", "Under Review"].includes(request.status));

  return (
    <div className="app-container">
      <header className="app-header">
        <div>
          <h1>FDR Management Portal</h1>
          <p>
            Security Deposit and Fixed Deposit Receipt Management
          </p>
        </div>

        <div className="account-actions"><span className="signed-in-user">{user.display_name}<small>{user.username}</small></span>
        <button
          className="refresh-button"
          onClick={loadFdrs}
          disabled={loading || busy}
        >
          {loading ? "Loading..." : "Refresh Data"}
        </button>
        <button className="secondary-button logout-button" disabled={busy || signingOut} onClick={onLogout}><LogOut size={17} aria-hidden="true" />{signingOut ? "Signing out..." : "Sign Out"}</button></div>
      </header>

      {message && (
        <div className={`message-banner ${messageIsError ? "error-banner" : "success-banner"}`} role={messageIsError ? "alert" : "status"}>
          {message}
        </div>
      )}

      <div className="role-dashboard-heading"><h2>{view === "college" ? <GraduationCap size={22} aria-hidden="true" /> : <ShieldCheck size={22} aria-hidden="true" />}{view === "college" ? "College Dashboard" : "Officer Dashboard"}</h2></div>

      {/* Summary statistics */}
      <section className="stats-grid">
        {stats.map((stat) => (
          <div
            className={`stat-card ${stat.className}`}
            key={stat.label}
          >
            <p>{stat.label}</p>
            <h2>{stat.value}</h2>
          </div>
        ))}
      </section>

      <PortfolioIntelligence data={intelligence} loading={loading} onSelect={selectFdr} />

      {/* College registration form */}
      {view === "college" && (
        <section className="panel" ref={registration}>
          <h2>{editingId ? `Edit FDR #${editingId}` : "Register New FDR"}</h2>
          <p className="panel-description">
            Enter the institution's fixed deposit details.
          </p>

          <form onSubmit={handleSubmit} className="fdr-form"><fieldset disabled={busy || loading}>
            <div className="form-grid">
              <div className="form-field">
                <label htmlFor="institution_name">
                  Institution Name
                </label>
                <input
                  id="institution_name"
                  name="institution_name"
                  value={form.institution_name}
                  onChange={handleChange}
                  placeholder="Enter institution name"
                  minLength={2}
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="certificate_number">
                  Certificate Number
                </label>
                <input
                  id="certificate_number"
                  name="certificate_number"
                  value={form.certificate_number}
                  onChange={handleChange}
                  placeholder="e.g. FDR-2026-001"
                  minLength={2}
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="bank_name">Bank Name</label>
                <input
                  id="bank_name"
                  name="bank_name"
                  value={form.bank_name}
                  onChange={handleChange}
                  placeholder="Enter bank name"
                  minLength={2}
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="amount">FDR Amount (₹)</label>
                <input
                  id="amount"
                  name="amount"
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={form.amount}
                  onChange={handleChange}
                  placeholder="Enter amount"
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="maturity_date">Maturity Date</label>
                <input
                  id="maturity_date"
                  name="maturity_date"
                  type="date"
                  value={form.maturity_date}
                  onChange={handleChange}
                  required
                />
              </div>
            </div>

            <button
              type="submit"
              className="primary-button"
              disabled={loading || busy}
            >
              {editingId ? "Save Corrections" : "Register FDR"}
            </button>
            {editingId && <button type="button" className="secondary-button" onClick={clearForm}>Cancel Edit</button>}
            </fieldset>
          </form>
        </section>
      )}

      {/* FDR records */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>
              {view === "college"
                ? "All FDR Records"
                : "FDRs Requiring Review"}
            </h2>

            <p className="panel-description">
              {view === "college"
                ? "Track the status of registered FDRs."
                : "Review submitted FDRs and verify their documents."}
            </p>
          </div>

          <span className="record-count">
            {visibleFdrs.length} record
            {visibleFdrs.length === 1 ? "" : "s"}
          </span>
        </div>

        <div className="filter-bar">
          <label>Search FDRs<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Institution, certificate or bank" /></label>
          <label>FDR status<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            {(view === "officer" ? ["All", "Submitted", "Under Review"] : ["All", "Draft", "Submitted", "Under Review", "Correction Required", "Verified", "Rejected", "Released", "Refunded"]).map((status) => <option key={status}>{status}</option>)}
          </select></label>
        </div>

        {loading ? (
          <p>Loading FDR records...</p>
        ) : visibleFdrs.length === 0 ? (
          <div className="empty-state">
            <h3>No FDR records found</h3>
            <p>
              {view === "officer"
                ? "There are no submitted FDRs waiting for review."
                : "Register your first FDR using the form above."}
            </p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Certificate Number</th>
                  <th>Institution</th>
                  <th>Bank</th>
                  <th>Amount</th>
                  <th>Maturity Date</th>
                  <th>Status</th>
                  <th>Review Note</th>
                  <th>Action</th>
                </tr>
              </thead>

              <tbody>
                {visibleFdrs.map((fdr) => (
                  <tr key={fdr.id}>
                    <td><button className="link-button" onClick={() => selectFdr(fdr.id)}>{fdr.certificate_number}</button></td>
                    <td>{fdr.institution_name}</td>
                    <td>{fdr.bank_name}</td>
                    <td>
                      {Number(fdr.amount).toLocaleString("en-IN", {
                        style: "currency",
                        currency: "INR",
                        maximumFractionDigits: 2,
                      })}
                    </td>
                    <td>{fdr.maturity_date}</td>
                    <td>
                      <span
                        className={`status-badge status-${fdr.status
                          .toLowerCase()
                          .replace(/\s+/g, "-")}`}
                      >
                        {fdr.status}
                      </span>
                    </td>
                    <td>{fdr.review_note || "—"}</td>
                    <td><div className="record-actions">{renderActions(fdr)}<button className="secondary-button" onClick={() => selectFdr(fdr.id)}>Details</button></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div id="fdr-details" ref={details}>{selectedFdr && <FdrDetails key={selectedFdr.id} fdr={selectedFdr} view={view}
        pending={pendingRequest} busy={busy || loading} perform={perform} onClose={() => setSelectedId(null)} />}</div>
      <RequestHistory title="Renewal / Replacement Requests" type="renewal" requests={renewals} view={view}
        busy={busy || loading} perform={perform} onSelect={selectFdr} />
      <RequestHistory title="Release / Refund Requests" type="release" requests={releases} view={view}
        busy={busy || loading} perform={perform} onSelect={selectFdr} />

      <footer className="app-footer">
        <p>
          FDR Management Portal
        </p>
        <p>
          Demonstration environment | Shared portfolio
        </p>
      </footer>
    </div>
  );
}

export default function App() {
  return <Auth>{(user, onLogout, signingOut) => <Dashboard key={user.id} user={user} onLogout={onLogout} signingOut={signingOut} />}</Auth>;
}
