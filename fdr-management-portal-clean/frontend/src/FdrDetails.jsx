import React, { useEffect, useState } from "react";
import axios from "axios";
import { Check, ClipboardCheck, Clock, Send, User, X } from "lucide-react";

axios.defaults.withCredentials = true;

export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
export const currency = (amount) => Number(amount).toLocaleString("en-IN", {
  style: "currency", currency: "INR", maximumFractionDigits: 2,
});

export function errorMessage(error) {
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => `${item.loc?.slice(1).join(" ") || "Field"}: ${item.msg}`).join(". ");
  return error.response ? "The request could not be completed. Please try again." : error.message || "Cannot reach the API.";
}

export function RequestHistory({ title, type, requests, view, busy, perform, onSelect }) {
  const [filter, setFilter] = useState("All");
  const visible = requests.filter((request) => filter === "All" || request.status === filter);
  return <section className="panel">
    <div className="panel-heading">
      <h2>{title}</h2>
      <label className="filter-label">Request status
        <select value={filter} onChange={(event) => setFilter(event.target.value)}>
          {["All", "Pending", "Under Review", "Approved", "Rejected"].map((status) => <option key={status}>{status}</option>)}
        </select>
      </label>
    </div>
    <p className="muted-text">{requests.filter((request) => ["Pending", "Under Review"].includes(request.status)).length} pending / under review</p>
    {visible.length === 0 ? <p className="empty-state">No {filter === "All" ? "" : filter.toLowerCase()} requests.</p> :
      <div className="table-wrapper"><table>
        <thead><tr><th>Request / FDR</th><th>Details</th><th>Notes</th><th>Status & Audit Timeline</th><th>Officer Decision</th></tr></thead>
        <tbody>{visible.map((request) => <RequestRow key={request.id} {...{ request, type, view, busy, perform, onSelect }} />)}</tbody>
      </table></div>}
  </section>;
}

function RequestRow({ request, type, view, busy, perform, onSelect }) {
  const [note, setNote] = useState("");
  async function review(action) {
    const success = await perform(async () => {
      if (action === "reject" && !note.trim()) throw new Error("Enter a reason before rejecting the request.");
      await axios.post(`${API_URL}/${type}-requests/${request.id}/review`, { action, review_note: note });
    }, `${request.kind} request ${action === "start_review" ? "is under review" : action === "approve" ? "approved" : "rejected"}.`);
    if (success) setNote("");
  }
  return <tr>
    <td><strong>{request.kind} #{request.id}</strong><br />{request.institution_name}<br />
      <button className="link-button" onClick={() => onSelect(request.fdr_id)}>Open FDR #{request.fdr_id}</button>
      <small>{request.created_at} UTC</small>
    </td>
    <td>{type === "renewal" ? <>
      <span>{request.previous_certificate_number} to <strong>{request.certificate_number}</strong></span>
      <br />{request.previous_bank_name} to {request.bank_name}
      <br />{currency(request.previous_amount)} to {currency(request.amount)}
      <br />{request.previous_maturity_date} to {request.maturity_date}
    </> : <>{request.certificate_number}<br />{currency(request.amount)}</>}</td>
    <td>{request.notes || request.reason || "None"}{request.review_note && <p><strong>Officer:</strong> {request.review_note}</p>}</td>
    <td><span className={`status-badge status-${request.status.toLowerCase().replace(/\s+/g, "-")}`}>{request.status}</span>
      <ol className="audit-timeline" aria-label={`${request.kind} request ${request.id} audit timeline`}>
        {(request.timeline || []).map((event) => <li key={event.id} className={`audit-${event.decision.toLowerCase().replace(/\s+/g, "-")}`}>
          <span className="audit-marker">{event.decision === "Submitted" ? <Send size={12} /> : event.decision === "Under Review" ? <Clock size={12} /> : event.decision === "Approved" ? <Check size={12} /> : <X size={12} />}</span>
          <strong>{event.decision}</strong><small><User size={12} aria-hidden="true" />{event.actor}</small>
          <time dateTime={event.created_at.endsWith("Z") ? event.created_at : `${event.created_at.replace(" ", "T")}Z`}>{event.created_at.replace("T", " ").replace("Z", "")} UTC</time>
          {event.remarks && <p>{event.remarks}</p>}
        </li>)}
      </ol>
    </td>
    <td>{view === "officer" && ["Pending", "Under Review"].includes(request.status) ? <div className="request-decision">
      <label>Decision note
        <textarea aria-label={`Decision note for ${type} request ${request.id}`} value={note} maxLength={2000}
          onChange={(event) => setNote(event.target.value)} disabled={busy} />
      </label>
      <div className="action-group">
        {request.status === "Pending" ? <button disabled={busy} onClick={() => review("start_review")}><ClipboardCheck size={16} aria-hidden="true" />Start Review</button> : <>
          <button disabled={busy} className="success-button" onClick={() => review("approve")}><Check size={16} aria-hidden="true" />Approve</button>
          <button disabled={busy} className="danger-button" onClick={() => review("reject")}><X size={16} aria-hidden="true" />Reject</button>
        </>}
      </div>
    </div> : request.reviewed_at ? <small>{request.reviewed_at} UTC</small> : <span className="muted-text">Awaiting officer review</span>}</td>
  </tr>;
}

export default function FdrDetails({ fdr, view, pending, busy, perform, onClose }) {
  const [documents, setDocuments] = useState([]);
  const [documentError, setDocumentError] = useState("");
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [file, setFile] = useState(null);
  const [kind, setKind] = useState("Renewal");
  const [request, setRequest] = useState({ certificate_number: fdr.certificate_number, bank_name: fdr.bank_name,
    amount: fdr.amount, maturity_date: "", notes: "", reason: "" });
  const [review, setReview] = useState({ action: "verified", review_note: "" });

  useEffect(() => {
    let active = true;
    setDocumentsLoading(true);
    axios.get(`${API_URL}/fdrs/${fdr.id}/documents`).then((response) => {
      if (active) { setDocuments(response.data); setDocumentError(""); }
    }).catch((error) => { if (active) setDocumentError(errorMessage(error)); })
      .finally(() => { if (active) setDocumentsLoading(false); });
    return () => { active = false; };
  }, [fdr.id]);

  async function upload(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const success = await perform(async () => {
      if (!file) throw new Error("Choose a certificate file.");
      if (file.size > 10 * 1024 * 1024) throw new Error("Certificate must be 10 MB or smaller.");
      const data = new FormData();
      data.append("file", file);
      await axios.post(`${API_URL}/fdrs/${fdr.id}/documents`, data);
    }, "Certificate uploaded successfully.");
    if (success) {
      setFile(null);
      formElement.reset();
      try {
        const response = await axios.get(`${API_URL}/fdrs/${fdr.id}/documents`);
        setDocuments(response.data);
        setDocumentError("");
      } catch (error) { setDocumentError(errorMessage(error)); }
    }
  }

  async function download(document) {
    await perform(async () => {
      const response = await axios.get(`${API_URL}/documents/${document.id}/download`, { responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const anchor = globalThis.document.createElement("a");
      anchor.href = url;
      anchor.download = document.filename;
      globalThis.document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, "Certificate downloaded.");
  }

  async function submitRequest(event) {
    event.preventDefault();
    const renewal = kind === "Renewal" || kind === "Replacement";
    const success = await perform(() => axios.post(`${API_URL}/fdrs/${fdr.id}/${renewal ? "renewal" : "release"}-requests`,
      renewal ? { kind, certificate_number: request.certificate_number, bank_name: request.bank_name,
        amount: Number(request.amount), maturity_date: request.maturity_date, notes: request.notes } : { kind, reason: request.reason }),
    `${kind} request submitted for officer approval.`);
    if (success) setRequest({ ...request, reason: "", notes: "" });
  }

  async function submitReview(event) {
    event.preventDefault();
    const success = await perform(() => axios.post(`${API_URL}/fdrs/${fdr.id}/review`, review), "FDR review saved.");
    if (success) setReview({ action: "verified", review_note: "" });
  }

  function changeRequest(event) { setRequest({ ...request, [event.target.name]: event.target.value }); }

  return <section className="panel fdr-details" aria-label="Selected FDR details">
    <div className="panel-heading">
      <div><h2>FDR #{fdr.id}: {fdr.certificate_number}</h2><p>{fdr.institution_name} | {fdr.status}</p></div>
      <button className="secondary-button" onClick={onClose}>Close Details</button>
    </div>
    <dl className="detail-summary"><div><dt>Bank</dt><dd>{fdr.bank_name}</dd></div>
      <div><dt>Amount</dt><dd>{currency(fdr.amount)}</dd></div>
      <div><dt>Maturity</dt><dd>{fdr.maturity_date}</dd></div></dl>
    {fdr.review_note && <p className="review-note"><strong>Officer note:</strong> {fdr.review_note}</p>}

    {view === "officer" && fdr.status === "Under Review" && <section className="detail-section">
      <h3>Review FDR</h3>
      <form onSubmit={submitReview}><fieldset disabled={busy}>
        <label>Review decision<select value={review.action} onChange={(event) => setReview({ ...review, action: event.target.value })}>
          <option value="verified">Verify</option><option value="correction_required">Request Corrections</option><option value="rejected">Reject</option>
        </select></label>
        <label>Review note<textarea value={review.review_note} required={review.action !== "verified"}
          onChange={(event) => setReview({ ...review, review_note: event.target.value })} /></label>
        <button type="submit">Save Review</button>
      </fieldset></form>
    </section>}

    <section className="detail-section"><h3>Certificates</h3>
      {documentError && <p role="alert" className="error-text">{documentError}</p>}
      {documentsLoading ? <p>Loading certificates...</p> : documents.length ? <ul className="document-list">
        {documents.map((document) => <li key={document.id}><span>{document.filename} <small>{(document.size / 1024).toFixed(1)} KB</small></span>
          <button disabled={busy} onClick={() => download(document)}>Download</button></li>)}
      </ul> : <p className="muted-text">No certificates uploaded.</p>}
      {view === "college" && <form onSubmit={upload}><fieldset disabled={busy}>
        <label>Certificate file (PDF, JPEG, PNG; up to 10 MB)
          <input type="file" accept=".pdf,.jpg,.jpeg,.png" required onChange={(event) => setFile(event.target.files[0] || null)} />
        </label><button type="submit" disabled={!file}>Upload Certificate</button>
      </fieldset></form>}
    </section>

    {view === "college" && <section className="detail-section"><h3>Renewal, Replacement, Release or Refund</h3>
      {fdr.status !== "Verified" ? <p className="muted-text">Requests are available for verified FDRs only.</p> :
        pending ? <p role="status">A request is pending officer approval. Its status is shown in the request history.</p> :
          <form onSubmit={submitRequest} className="request-form"><fieldset disabled={busy}>
            <label>Request type<select value={kind} onChange={(event) => setKind(event.target.value)}>
              {["Renewal", "Replacement", "Release", "Refund"].map((value) => <option key={value}>{value}</option>)}
            </select></label>
            {kind === "Renewal" || kind === "Replacement" ? <>
              <div className="form-grid">
                <label>New certificate number<input name="certificate_number" value={request.certificate_number} onChange={changeRequest} minLength={2} required /></label>
                <label>New bank name<input name="bank_name" value={request.bank_name} onChange={changeRequest} minLength={2} required /></label>
                <label>New amount<input name="amount" type="number" min="0.01" step="0.01" value={request.amount} onChange={changeRequest} required /></label>
                <label>New maturity date<input name="maturity_date" type="date" value={request.maturity_date} onChange={changeRequest} required /></label>
              </div>
              <label>Request notes<textarea name="notes" value={request.notes} onChange={changeRequest} maxLength={2000} /></label>
            </> : <label>Reason for {kind.toLowerCase()}<textarea name="reason" value={request.reason} onChange={changeRequest} minLength={3} maxLength={2000} required /></label>}
            <button type="submit">Submit {kind} Request</button>
          </fieldset></form>}
    </section>}
  </section>;
}