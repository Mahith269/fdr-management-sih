
import React, { useEffect, useState } from "react";
import axios from "axios";
import "./App.css";

const API_URL = "http://127.0.0.1:8000";

function App() {
  const [fdrs, setFdrs] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState("college");

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

      const [fdrResponse, alertResponse] = await Promise.all([
        axios.get(`${API_URL}/fdrs`),
        axios.get(`${API_URL}/fdrs/alerts`),
      ]);

      setFdrs(fdrResponse.data);
      setAlerts(alertResponse.data.alerts || []);
      setMessage("");
    } catch (error) {
      console.error("Error loading FDR data:", error);
      setMessage(
        "Could not load FDR data. Make sure the FastAPI backend is running."
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadFdrs();
  }, []);

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
    setMessage("");

    try {
      await axios.post(`${API_URL}/fdrs`, {
        institution_name: form.institution_name.trim(),
        certificate_number: form.certificate_number.trim(),
        bank_name: form.bank_name.trim(),
        amount: Number(form.amount),
        maturity_date: form.maturity_date,
      });

      setForm({
        institution_name: "",
        certificate_number: "",
        bank_name: "",
        amount: "",
        maturity_date: "",
      });

      setMessage("FDR registered successfully.");
      await loadFdrs();
    } catch (error) {
      console.error("Error registering FDR:", error);

      const detail = error.response?.data?.detail;
      setMessage(
        typeof detail === "string"
          ? detail
          : "Could not register FDR. Check the details and try again."
      );
    }
  }

  // Submit an FDR or perform an officer review action
  async function updateStatus(id, action) {
    setMessage("");

    try {
      if (action === "submit") {
        await axios.post(`${API_URL}/fdrs/${id}/submit`);
      } else {
        let reviewNote = "";

        if (action === "correction_required") {
          reviewNote = window.prompt(
            "Enter the corrections required:",
            ""
          );

          if (reviewNote === null) {
            return;
          }

          if (!reviewNote.trim()) {
            setMessage("Please enter the corrections required.");
            return;
          }
        } else if (action === "verified") {
          reviewNote = window.prompt(
            "Enter verification remarks (optional):",
            "Documents verified in demo"
          );

          if (reviewNote === null) {
            return;
          }
        }

        await axios.post(`${API_URL}/fdrs/${id}/review`, {
          action,
          review_note: reviewNote,
        });
      }

      setMessage("FDR status updated successfully.");
      await loadFdrs();
    } catch (error) {
      console.error("Error updating FDR:", error);

      const detail = error.response?.data?.detail;
      setMessage(
        typeof detail === "string"
          ? detail
          : "Could not update FDR status. Check the backend API."
      );
    }
  }

  // Render actions based on the selected dashboard and current status
  function renderActions(fdr) {
    if (view === "college") {
      if (
        fdr.status === "Draft" ||
        fdr.status === "Correction Required"
      ) {
        return (
          <button
            className="action-button"
            onClick={() => updateStatus(fdr.id, "submit")}
          >
            Submit FDR
          </button>
        );
      }

      return <span className="muted-text">No action available</span>;
    }

    if (fdr.status === "Submitted") {
      return (
        <button
          className="action-button"
          onClick={() => updateStatus(fdr.id, "under_review")}
        >
          Start Review
        </button>
      );
    }

    if (fdr.status === "Under Review") {
      return (
        <div className="action-group">
          <button
            className="action-button success-button"
            onClick={() => updateStatus(fdr.id, "verified")}
          >
            Verify
          </button>

          <button
            className="action-button danger-button"
            onClick={() => updateStatus(fdr.id, "correction_required")}
          >
            Request Corrections
          </button>
        </div>
      );
    }

    return <span className="muted-text">No action available</span>;
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
    view === "officer"
      ? fdrs.filter(
          (fdr) =>
            fdr.status === "Submitted" ||
            fdr.status === "Under Review"
        )
      : fdrs;

  // Display the maturity alert
  function renderMaturityAlert(alert) {
    if (alert.alert_type === "Matured") {
      return (
        <span className="alert-matured">
          Matured {Math.abs(alert.days_remaining)} day(s) ago
        </span>
      );
    }

    return (
      <span className="alert-upcoming">
        Matures in {alert.days_remaining} day(s)
      </span>
    );
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <div>
          <h1>FDR Management Portal</h1>
          <p>
            Security Deposit and Fixed Deposit Receipt Management
          </p>
        </div>

        <button
          className="refresh-button"
          onClick={loadFdrs}
          disabled={loading}
        >
          {loading ? "Loading..." : "Refresh Data"}
        </button>
      </header>

      {message && (
        <div className="message-banner" role="status">
          {message}
        </div>
      )}

      {/* Dashboard navigation */}
      <nav className="dashboard-tabs">
        <button
          className={view === "college" ? "tab active-tab" : "tab"}
          onClick={() => setView("college")}
        >
          College Dashboard
        </button>

        <button
          className={view === "officer" ? "tab active-tab" : "tab"}
          onClick={() => setView("officer")}
        >
          Officer Dashboard
        </button>
      </nav>

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

      {/* NEW: FDR maturity alerts */}
      <section className="panel maturity-panel">
        <div className="panel-heading">
          <div>
            <h2>FDR Maturity Alerts</h2>
            <p>
              Verified FDRs that have matured or are due within
              the next 30 days.
            </p>
          </div>

          <span className="alert-count">
            {alerts.length} alert{alerts.length === 1 ? "" : "s"}
          </span>
        </div>

        {loading ? (
          <p>Loading maturity alerts...</p>
        ) : alerts.length === 0 ? (
          <div className="empty-state">
            <h3>No maturity alerts</h3>
            <p>
              No verified FDRs are currently due for attention.
            </p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Certificate Number</th>
                  <th>Institution</th>
                  <th>Maturity Date</th>
                  <th>Alert</th>
                </tr>
              </thead>

              <tbody>
                {alerts.map((alert) => (
                  <tr key={alert.id}>
                    <td>{alert.certificate_number}</td>
                    <td>{alert.institution_name}</td>
                    <td>{alert.maturity_date}</td>
                    <td>{renderMaturityAlert(alert)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* College registration form */}
      {view === "college" && (
        <section className="panel">
          <h2>Register New FDR</h2>
          <p className="panel-description">
            Enter the institution's fixed deposit details.
          </p>

          <form onSubmit={handleSubmit} className="fdr-form">
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
              disabled={loading}
            >
              Register FDR
            </button>
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
                    <td>{fdr.certificate_number}</td>
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
                    <td>{renderActions(fdr)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <footer className="app-footer">
        <p>
          FDR Management Portal · Hackathon Prototype
        </p>
        <p>
          Demo dashboard only. Authentication and role-based
          access must be implemented before production use.
        </p>
      </footer>
    </div>
  );
}

export default App;
