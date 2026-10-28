import React, { useEffect, useRef, useState } from "react";
import axios from "axios";
import { ArrowRight, Bot, CircleAlert, Radar, Search } from "lucide-react";
import { API_URL, currency, errorMessage } from "./FdrDetails";

const questions = [
  ["maturing_30", "How much money matures in the next 30 days?"],
  ["attention", "Which FDRs need immediate attention?"],
  ["pending_releases", "Show all pending release requests."],
  ["top_bank", "Which bank holds the highest principal amount?"],
  ["maturing_7", "How much money matures in the next 7 days?"],
  ["maturing_90", "How much money matures in the next 90 days?"],
];

export default function PortfolioIntelligence({ data, loading, onSelect }) {
  const [risk, setRisk] = useState("All");
  const [action, setAction] = useState("maturing_30");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);

  useEffect(() => {
    sequence.current += 1;
    setAnswer(null);
    setError("");
    setAsking(false);
    return () => { sequence.current += 1; };
  }, [data]);

  async function ask(event) {
    event.preventDefault();
    const requestId = ++sequence.current;
    setAsking(true);
    setError("");
    setAnswer(null);
    try {
      const response = await axios.post(`${API_URL}/assistant/query`, question.trim() ? { question } : { action });
      if (requestId === sequence.current) setAnswer(response.data);
    } catch (failure) {
      if (requestId === sequence.current) setError(errorMessage(failure));
    } finally {
      if (requestId === sequence.current) setAsking(false);
    }
  }

  const records = (data?.records || []).filter((record) => risk === "All" || record.risk === risk);
  function recordLink(id, label) {
    return <a href="#fdr-details" onClick={(event) => { event.preventDefault(); onSelect(id); }}>{label}<ArrowRight size={14} aria-hidden="true" /></a>;
  }

  return <>
    <section className="panel intelligence-panel" aria-labelledby="radar-title">
      <div className="panel-heading">
        <div><h2 id="radar-title" className="icon-heading"><Radar size={24} aria-hidden="true" />Maturity Risk Radar</h2>
          <p className="muted-text">Verified portfolio | {data?.as_of || "Awaiting data"}</p></div>
        <span className="radar-principal">{currency(data?.total_principal || 0)}<small>Verified principal</small></span>
      </div>
      {loading ? <p role="status">Loading portfolio intelligence...</p> : !data ? <p role="status">Portfolio intelligence is unavailable.</p> : <>
        <div className="radar-overview">
          <div className="risk-score-block">
            <div className="risk-gauge" style={{ "--score": `${data.risk_score}%` }} role="img" aria-label={`Portfolio maturity risk ${data.risk_score} out of 100`}>
              <span><strong>{data.risk_score}</strong><small>/ 100</small></span>
            </div>
            <div><h3>Portfolio Risk Score</h3><span className="muted-text">{data.records.length ? "Principal-weighted maturity exposure" : "No verified holdings"}</span>
              <p className="overdue-total"><CircleAlert size={16} aria-hidden="true" />{currency(data.overdue_principal)} overdue</p></div>
          </div>
          <dl className="maturity-windows">{data.windows.map((window) => <div key={window.days}>
            <dt>Next {window.days} days</dt><dd>{currency(window.amount)}</dd><small>{window.count} FDR{window.count === 1 ? "" : "s"}</small>
          </div>)}</dl>
        </div>
        <div className="risk-distribution" role="img" aria-label={Object.entries(data.categories).map(([name, count]) => `${name}: ${count}`).join(", ")}>
          {Object.entries(data.categories).map(([name, count]) => count > 0 && <span key={name} className={`risk-segment risk-${name.toLowerCase()}`} style={{ flex: count }} />)}
        </div>
        <div className="radar-controls"><div className="risk-legend">{Object.entries(data.categories).map(([name, count]) =>
          <span key={name}><i className={`risk-dot risk-${name.toLowerCase()}`} />{name} <strong>{count}</strong></span>)}</div>
          <label>Risk category<select value={risk} onChange={(event) => setRisk(event.target.value)}>
            {["All", "Critical", "High", "Upcoming", "Safe"].map((name) => <option key={name}>{name}</option>)}
          </select></label></div>
        {records.length ? <div className="table-wrapper"><table className="radar-table">
          <thead><tr><th>FDR / Institution</th><th>Principal</th><th>Maturity</th><th>Days Remaining</th><th>Risk</th><th>Suggested Action</th></tr></thead>
          <tbody>{records.map((record) => <tr key={record.id}>
            <td>{recordLink(record.id, record.certificate_number)}<small>{record.institution_name} | {record.bank_name}</small></td>
            <td>{currency(record.amount)}</td><td>{record.maturity_date}</td>
            <td>{record.days_remaining < 0 ? `${Math.abs(record.days_remaining)} overdue` : record.days_remaining === 0 ? "Today" : `${record.days_remaining} days`}</td>
            <td><span className={`risk-badge risk-${record.risk.toLowerCase()}`}>{record.risk}</span></td>
            <td>{record.suggested_action}</td>
          </tr>)}</tbody>
        </table></div> : <p className="empty-state">No {risk === "All" ? "verified" : risk.toLowerCase()} FDRs.</p>}
        <details className="risk-rules"><summary>Scoring Rules & Scope</summary><p>{data.rules}</p></details>
      </>}
    </section>

    <section className="panel assistant-panel" aria-labelledby="assistant-title">
      <div className="panel-heading"><h2 id="assistant-title" className="icon-heading"><Bot size={24} aria-hidden="true" />Portfolio Intelligence Assistant</h2>
        <span className="assistant-source">Predefined portfolio queries</span></div>
      <form className="assistant-form" onSubmit={ask}>
        <label>Portfolio query<select value={action} onChange={(event) => { setAction(event.target.value); setQuestion(""); }} disabled={asking || loading}>
          {questions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <label>Question<input value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={500} placeholder="How much money matures in the next 30 days?" disabled={asking || loading} /></label>
        <button type="submit" disabled={asking || loading || !data}><Search size={17} aria-hidden="true" />{asking ? "Querying..." : "Ask Portfolio"}</button>
      </form>
      {error && <p className="error-text" role="alert">{error}</p>}
      {answer && <div className="assistant-answer" role="status">
        <p>{answer.answer}</p>
        {answer.supported && <><div className="answer-totals"><strong>{currency(answer.amount)}</strong><span>{answer.count} record{answer.count === 1 ? "" : "s"}</span><small>As of {answer.as_of}</small></div>
          {answer.records.length ? <ul className="answer-records">{answer.records.map((record) => <li key={record.request_id || record.id}>
            <span>{recordLink(record.id, record.certificate_number)}<small>{record.institution_name}{record.request_id ? ` | ${record.kind} #${record.request_id} | ${record.status}` : ""}</small></span><strong>{currency(record.amount)}</strong>
          </li>)}</ul> : <p className="muted-text">No matching records.</p>}</>}
      </div>}
    </section>
  </>;
}