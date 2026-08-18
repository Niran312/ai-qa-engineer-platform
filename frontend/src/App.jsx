import React, { useState, useEffect, useRef } from 'react';
import { Bot, Plus, Network, LayoutGrid, FileText, Terminal, CheckCircle2, AlertCircle, X, ChevronRight, Eye, Download, Folders } from 'lucide-react';
import Dashboard from './components/Dashboard';
import CrawlerMap from './components/CrawlerMap';
import TestCaseTable from './components/TestCaseTable';
import { apiFetch, resolveAssetUrl } from './api';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard'); // 'dashboard' | 'scan-details'
  const [selectedScanId, setSelectedScanId] = useState(null);
  const [scanDetails, setScanDetails] = useState(null);
  
  // Results tabs: 'map' | 'pages' | 'testcases' | 'logs'
  const [resultsTab, setResultsTab] = useState('map');
  const [selectedPageNode, setSelectedPageNode] = useState(null);
  const [lightboxImg, setLightboxImg] = useState(null);
  const [loadingNewScan, setLoadingNewScan] = useState(false);
  
  const pollingRef = useRef(null);

  const [liveTimers, setLiveTimers] = useState({
    totalElapsed: 0,
    stageElapsed: 0,
    featureElapsed: 0,
    opElapsed: 0,
    llmElapsed: 0
  });

  const formatTime = (secs) => {
    if (isNaN(secs) || secs < 0) return '00:00:00';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    return [
      h.toString().padStart(2, '0'),
      m.toString().padStart(2, '0'),
      s.toString().padStart(2, '0')
    ].join(':');
  };

  useEffect(() => {
    let interval = null;
    if (scanDetails && (scanDetails.status === 'running' || scanDetails.status === 'pending')) {
      interval = setInterval(() => {
        const now = Date.now();
        const perf = scanDetails.perf_data || {};
        
        const total = perf.started_at ? Math.max(0, Math.floor((now - perf.started_at) / 1000)) : 0;
        const stage = perf.stage_started_at ? Math.max(0, Math.floor((now - perf.stage_started_at) / 1000)) : 0;
        const feat = perf.feature_started_at ? Math.max(0, Math.floor((now - perf.feature_started_at) / 1000)) : 0;
        const op = perf.op_started_at ? Math.max(0, Math.floor((now - perf.op_started_at) / 1000)) : 0;
        const llm = perf.llm_started_at ? Math.max(0, Math.floor((now - perf.llm_started_at) / 1000)) : 0;
        
        setLiveTimers({
          totalElapsed: total,
          stageElapsed: stage,
          featureElapsed: feat,
          opElapsed: op,
          llmElapsed: llm
        });
      }, 1000);
    } else {
      if (scanDetails && scanDetails.status === 'completed' && scanDetails.perf_data?.final_summary) {
        const summary = scanDetails.perf_data.final_summary;
        setLiveTimers({
          totalElapsed: Math.floor(summary.scan?.duration || 0),
          stageElapsed: 0,
          featureElapsed: 0,
          opElapsed: 0,
          llmElapsed: 0
        });
      } else {
        setLiveTimers({
          totalElapsed: 0,
          stageElapsed: 0,
          featureElapsed: 0,
          opElapsed: 0,
          llmElapsed: 0
        });
      }
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [scanDetails]);

  const parseMcpProgress = (logs) => {
    if (!logs) return [];
    
    const stepDefinitions = [
      { id: 'startup', label: 'MCP process started', startKey: 'Starting Playwright MCP process', endKey: 'Starting Playwright MCP process' },
      { id: 'stdio', label: 'Connecting stdio transport', startKey: 'Connecting stdio transport', endKey: 'Connecting stdio transport' },
      { id: 'session', label: 'MCP session initialized', startKey: 'MCP session initialization', endKey: 'MCP session initialization' },
      { id: 'tools', label: 'MCP tools loaded', startKey: 'Loading MCP tools', endKey: 'Loading MCP tools' },
      { id: 'browser', label: 'Chromium launched', startKey: 'Launching Chromium', endKey: 'Launching Chromium' },
      { id: 'navigate', label: 'Navigated to target page', startKey: 'Navigating to', endKey: 'Navigating to' },
      { id: 'snapshot', label: 'Page snapshot captured', startKey: 'Capturing page snapshot', endKey: 'Capturing page snapshot' }
    ];
    
    const parsedSteps = stepDefinitions.map(def => {
      let state = 'pending';
      let duration = '';
      
      const endLine = logs.find(l => l.includes('[MCP] [END] ' + def.endKey) || (def.id === 'navigate' && l.includes('[MCP] [END] Navigating to')) || (def.id === 'startup' && l.includes('[MCP] [END] Starting Playwright MCP process')));
      const startLine = logs.find(l => l.includes('[MCP] [START] ' + def.startKey) || (def.id === 'navigate' && l.includes('[MCP] [START] Navigating to')));
      
      if (endLine) {
        state = 'completed';
        const match = endLine.match(/duration=([\d.]+)s/);
        if (match) {
          duration = match[1] + 's';
        }
      } else if (startLine) {
        state = 'running';
      }
      
      return {
        ...def,
        state,
        duration
      };
    });
    
    logs.forEach(l => {
      if (l.includes('[MCP] [START] Exploring feature:')) {
        const featName = l.split('Exploring feature:')[1].trim();
        const existing = parsedSteps.find(s => s.label === `Exploring ${featName}`);
        if (!existing) {
          const endLine = logs.find(el => el.includes('[MCP] [END] Exploring feature: ' + featName));
          let state = 'running';
          let duration = '';
          if (endLine) {
            state = 'completed';
            const match = endLine.match(/duration=([\d.]+)s/);
            if (match) duration = match[1] + 's';
          }
          parsedSteps.push({
            id: `explore-${featName}`,
            label: `Exploring ${featName}`,
            state,
            duration
          });
        }
      }
    });

    // Behavior Engine fallback: surfaced explicitly so the live view never looks like MCP
    // silently succeeded when it actually failed and Playwright Core took over.
    if (logs.some(l => l.includes('[BEHAVIOR-ENGINE] Switching to PLAYWRIGHT_CORE'))) {
      parsedSteps.push({
        id: 'engine-switch',
        label: 'MCP unavailable — switched to Playwright Core fallback',
        state: 'completed',
        duration: ''
      });
    }

    logs.forEach(l => {
      const navMatch = l.match(/\[PW-CORE\] \[feature=(.+?)\] browser_navigate (STARTED|COMPLETED|FAILED)/);
      if (navMatch) {
        const featName = navMatch[1];
        const label = `Playwright Core: ${featName}`;
        const existing = parsedSteps.find(s => s.label === label);
        if (!existing) {
          const completedLine = logs.find(el => el.includes(`[PW-CORE] [feature=${featName}] browser_navigate COMPLETED`));
          const failedLine = logs.find(el => el.includes(`[PW-CORE] [feature=${featName}] browser_navigate FAILED`));
          let state = 'running';
          let duration = '';
          if (completedLine) {
            state = 'completed';
            const m = completedLine.match(/duration=([\d.]+)s/);
            if (m) duration = m[1] + 's';
          } else if (failedLine) {
            state = 'completed';
            duration = 'failed';
          }
          parsedSteps.push({ id: `pwcore-${featName}`, label, state, duration });
        }
      }
    });

    return parsedSteps;
  };

  // Fetch scans list (used only to auto-resume the most recent scan on load - the visible
  // scan history UI has been removed, but this underlying lookup is kept).
  const fetchScansList = async () => {
    try {
      const res = await apiFetch('/api/scans');
      if (res.ok) {
        return await res.json();
      }
    } catch (e) {
      console.error("Error fetching scans list", e);
    }
    return [];
  };

  // Fetch single scan details
  const fetchScanDetails = async (id) => {
    try {
      const res = await apiFetch(`/api/scan/${id}`);
      if (res.ok) {
        const data = await res.json();
        // Normalize the shape defensively so every downstream render (app_map.nodes,
        // test_cases.map, progress_log.map, ...) always has a safe default to work with,
        // even if the backend ever returns a partial/malformed payload - a bad response here
        // must not be able to blank the whole workspace.
        setScanDetails({
          ...data,
          app_map: data.app_map && typeof data.app_map === 'object' ? data.app_map : { nodes: [], edges: [] },
          test_cases: Array.isArray(data.test_cases) ? data.test_cases : [],
          progress_log: Array.isArray(data.progress_log) ? data.progress_log : [],
        });

        // Reset page nodes selected details when changing scan
        if (selectedScanId !== id) {
          setSelectedPageNode(null);
        }

        // Stop polling if completed or failed
        if (data.status === 'completed' || data.status === 'failed') {
          stopPolling();
          fetchScansList();
        }
      } else {
        console.error(`Error fetching scan details: HTTP ${res.status}`);
      }
    } catch (e) {
      console.error("Error fetching scan details", e);
    }
  };

  // Start polling
  const startPolling = (id) => {
    stopPolling();
    fetchScanDetails(id);
    pollingRef.current = setInterval(() => {
      fetchScanDetails(id);
    }, 1500);
  };

  // Stop polling
  const stopPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  useEffect(() => {
    fetchScansList().then(list => {
      if (list.length > 0) {
        // Load latest scan automatically
        setSelectedScanId(list[0].id);
        fetchScanDetails(list[0].id);
        setActiveTab('scan-details');
      }
    });
    return () => stopPolling();
  }, []);

  const handleStartScan = async (scanRequest) => {
    setLoadingNewScan(true);
    try {
      const res = await apiFetch('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scanRequest)
      });
      if (res.ok) {
        const data = await res.json();
        setSelectedScanId(data.scan_id);
        setActiveTab('scan-details');
        setResultsTab('map');
        // Begin progress tracking
        startPolling(data.scan_id);
      } else {
        const detail = await res.json().catch(() => null);
        console.error(`Failed to start scan: HTTP ${res.status}`, detail);
        window.alert(`Failed to start scan (HTTP ${res.status}): ${detail?.detail ? JSON.stringify(detail.detail) : 'Check the console for details.'}`);
      }
    } catch (e) {
      console.error("Failed to post new scan request", e);
      window.alert('Failed to reach the backend. Please check your connection and try again.');
    } finally {
      setLoadingNewScan(false);
    }
  };

  const clickNewScanMenu = () => {
    stopPolling();
    setActiveTab('dashboard');
    setScanDetails(null);
    setSelectedScanId(null);
  };

  // Calculate stepper states based on log cues and scan status
  const getStepperState = () => {
    if (!scanDetails) return [];
    const status = scanDetails.status.toLowerCase();
    const logs = scanDetails.progress_log || [];

    const steps = [
      { id: 'receive', label: 'Receive Scan Configuration', state: 'completed' },
      { id: 'login', label: 'Launch Browser & Navigate', state: 'pending' },
      { id: 'explore', label: 'Discover Pages & Elements', state: 'pending' },
      { id: 'reason', label: 'AI Test Case Generation', state: 'pending' },
      { id: 'excel', label: 'Export Styled Excel Report', state: 'pending' }
    ];

    if (status === 'failed') {
      steps.forEach(s => {
        if (s.state === 'pending') s.state = 'failed';
      });
      return steps;
    }

    // Determine current active steps based on logs
    let activeIndex = 0;
    logs.forEach(log => {
      if (log.includes('[CRAWLER]')) {
        activeIndex = 1;
      }
      if (log.includes('Scanning:')) {
        activeIndex = 2;
      }
      if (log.includes('[GENERATOR]')) {
        activeIndex = 3;
      }
      if (log.includes('styled Excel')) {
        activeIndex = 4;
      }
    });

    for (let i = 0; i < steps.length; i++) {
      if (i < activeIndex) {
        steps[i].state = 'completed';
      } else if (i === activeIndex) {
        steps[i].state = (status === 'completed') ? 'completed' : 'active';
      } else {
        steps[i].state = 'pending';
      }
    }

    if (status === 'completed') {
      steps.forEach(s => s.state = 'completed');
    }

    return steps;
  };

  const steps = getStepperState();
  const logPanelContainerRef = useRef(null);

  // Auto-scroll the live console log panel to its newest entry. This must only move the log
  // panel's own internal scroll (scrollTop), never the main page: scrollIntoView() on a nested
  // element walks up every scrollable ancestor - including the window itself - which was the
  // root cause of the main page repeatedly jumping down while a scan was running.
  useEffect(() => {
    const panel = logPanelContainerRef.current;
    if (panel) {
      panel.scrollTop = panel.scrollHeight;
    }
  }, [scanDetails?.progress_log]);

  return (
    <div className="app-container">
      
      {/* 1. Sidebar Navigation */}
      <aside className="sidebar">
        <div className="brand-section">
          <span className="brand-logo">🤖</span>
          <span className="brand-name">AI QA Engineer</span>
        </div>

        <ul className="sidebar-menu" style={{ flex: 1 }}>
          <li 
            className={`menu-item ${activeTab === 'dashboard' ? 'active' : ''}`}
            onClick={clickNewScanMenu}
          >
            <Plus className="menu-icon" />
            <span>New Scan</span>
          </li>
          
          {scanDetails && (
            <li 
              className={`menu-item ${activeTab === 'scan-details' ? 'active' : ''}`}
              onClick={() => setActiveTab('scan-details')}
            >
              <Bot className="menu-icon" />
              <span>Active Workspace</span>
            </li>
          )}
        </ul>
      </aside>

      {/* 2. Main Viewport Panel */}
      <main className="main-viewport">
        
        {/* Render Form */}
        {activeTab === 'dashboard' && (
          <>
            <header className="page-header">
              <h1 className="page-title">Generate Testing Suites</h1>
              <p className="page-subtitle">Input your URL and instruct the AI Agent to crawl and write styled test cases.</p>
            </header>
            <Dashboard onSubmit={handleStartScan} loading={loadingNewScan} />
          </>
        )}

        {/* Render Scanning & Workspace Results */}
        {activeTab === 'scan-details' && scanDetails && (
          <>
            <header className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
              <div>
                <h1 className="page-title" style={{ fontFamily: 'var(--font-title)', fontWeight: 800, fontSize: '1.4rem', letterSpacing: '-0.5px' }}>
                  UI DESIGN PROMPT - AI QA ENGINEER (APPLICATION MAP WORKSPACE)
                </h1>
                <p className="page-subtitle">
                  Scanned URL: <span style={{ color: 'var(--primary)', fontFamily: 'var(--font-mono)' }}>{scanDetails.url}</span>
                </p>
              </div>

              {scanDetails.excel_path && (
                <a
                  href={resolveAssetUrl(`/api/scan/${scanDetails.id}/download`)}
                  className="primary-btn"
                  style={{ textDecoration: 'none', background: 'linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%)', boxShadow: '0 4px 15px rgba(124, 58, 237, 0.3)', padding: '0.6rem 1.25rem', fontSize: '0.85rem' }}
                >
                  <Download size={14} style={{ marginRight: '6px', verticalAlign: 'middle' }} />
                  Download Excel Suite
                </a>
              )}
              

            </header>

            {/* Failed Scan State - the workspace has no other content to show once a scan
                fails before generating any app_map/test_cases, so without this the page
                looked blank below the header. */}
            {scanDetails.status === 'failed' && (
              <div className="glass-card" style={{ marginBottom: '1.5rem', padding: '1.25rem', border: '1px solid rgba(239, 68, 68, 0.3)', background: 'rgba(239, 68, 68, 0.08)' }}>
                <h3 style={{ fontFamily: 'var(--font-title)', fontSize: '1.1rem', fontWeight: 700, color: '#f87171', display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '0.5rem' }}>
                  <AlertCircle size={18} />
                  Scan Failed
                </h3>
                <p style={{ fontSize: '0.9rem', color: 'var(--text-main)', fontFamily: 'var(--font-mono)', wordBreak: 'break-word', margin: 0 }}>
                  {scanDetails.perf_data?.error_msg || 'The scan could not complete. Check Console Logs for details.'}
                </p>
              </div>
            )}

            {/* Live Scan Status Card */}
            {(scanDetails.status === 'running' || scanDetails.status === 'pending') && (
              <div className="glass-card" style={{ marginBottom: '1.5rem', padding: '1.25rem', border: '1px solid var(--primary-low)', background: 'rgba(17, 17, 24, 0.85)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1.5rem' }}>
                  <div>
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase', fontWeight: 600 }}>Agent Status</div>
                    <div style={{ fontSize: '1.2rem', fontWeight: 800, display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--primary)' }}>
                      <span style={{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#10b981', animation: 'pulse 1.5s infinite' }}></span>
                      RUNNING
                    </div>
                    <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      Scope: <span style={{ color: 'var(--text-main)', fontWeight: 600, textTransform: 'uppercase' }}>{scanDetails.perf_data?.scan_scope || 'ENTIRE_APPLICATION'}</span>
                    </div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase', fontWeight: 600 }}>Total Elapsed Time</div>
                    <div style={{ fontSize: '1.4rem', fontWeight: 800, fontFamily: 'var(--font-mono)' }}>{formatTime(liveTimers.totalElapsed)}</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase', fontWeight: 600 }}>Current Stage</div>
                    <div style={{ fontSize: '0.95rem', fontWeight: 700, color: 'var(--secondary)', margin: '2px 0' }}>{scanDetails.perf_data?.stage || 'Initializing'}</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      Stage Elapsed: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-main)' }}>{formatTime(liveTimers.stageElapsed)}</span>
                    </div>
                  </div>
                  
                  {scanDetails.perf_data?.feature && (
                    <div>
                      <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase', fontWeight: 600 }}>Current Feature ({scanDetails.perf_data.feature_progress || '0/0'})</div>
                      <div style={{ fontSize: '0.8rem', fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--text-main)' }} title={scanDetails.perf_data.feature}>
                        {scanDetails.perf_data.feature}
                      </div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        Feature Elapsed: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-main)' }}>{formatTime(liveTimers.featureElapsed)}</span>
                      </div>
                    </div>
                  )}

                  {scanDetails.perf_data?.operation && (
                    <div>
                      <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase', fontWeight: 600 }}>Current Operation</div>
                      <div style={{ fontSize: '0.85rem', fontWeight: 700, fontFamily: 'var(--font-mono)', color: '#34d399' }}>{scanDetails.perf_data.operation}</div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        Op Elapsed: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-main)' }}>{formatTime(liveTimers.opElapsed)}</span>
                      </div>
                    </div>
                  )}

                  {scanDetails.perf_data?.llm_stage && (
                    <div>
                      <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase', fontWeight: 600 }}>Gemini AI Processing</div>
                      <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#fb923c' }}>{scanDetails.perf_data.llm_stage}</div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        AI Elapsed: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-main)' }}>{formatTime(liveTimers.llmElapsed)}</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Stepper progress and CLI logs shown if Pending or Running */}
            {(scanDetails.status === 'pending' || scanDetails.status === 'running') && (
              <div className="scan-dashboard-grid" style={{ marginBottom: '2rem' }}>
                
                {/* Visual Stepper & MCP checklist */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                  <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    <h3 style={{ fontFamily: 'var(--font-title)', fontSize: '1.1rem', fontWeight: 700 }}>
                      Analysis Progress
                    </h3>
                    <div className="stepper-container">
                      {steps.map((s, idx) => (
                        <div key={s.id} className={`step-row ${s.state}`}>
                          <div className="step-indicator">
                            {s.state === 'completed' ? '✓' : idx + 1}
                          </div>
                          <span className="step-label">{s.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Playwright MCP Live Operations Checklist */}
                  {scanDetails.perf_data && (
                    <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem', border: '1px solid var(--primary-low)', background: 'rgba(17, 17, 24, 0.85)' }}>
                      <h3 style={{ fontFamily: 'var(--font-title)', fontSize: '1.05rem', fontWeight: 700, color: 'var(--primary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <span style={{ display: 'inline-block', width: '6px', height: '6px', borderRadius: '50%', backgroundColor: '#10b981', animation: 'pulse 1.5s infinite' }}></span>
                        Playwright MCP Live Operations
                      </h3>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', maxHeight: '350px', overflowY: 'auto', paddingRight: '4px' }}>
                        {parseMcpProgress(scanDetails.progress_log).map((item) => (
                          <div key={item.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.85rem' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: item.state === 'completed' ? 'var(--text-main)' : item.state === 'running' ? 'var(--secondary)' : 'var(--text-muted)' }}>
                              <span>
                                {item.state === 'completed' ? '✓' : item.state === 'running' ? '→' : '○'}
                              </span>
                              <span style={{ fontWeight: item.state === 'running' ? 700 : 500 }}>
                                {item.label}
                              </span>
                            </div>
                            {item.duration && (
                              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                                {item.duration}
                              </span>
                            )}
                            {item.state === 'running' && (
                              <span style={{ fontSize: '0.7rem', color: 'var(--secondary)', animation: 'pulse 1.5s infinite' }}>
                                active
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                      {scanDetails.perf_data?.feature_progress && (
                        <div style={{ borderTop: '1px solid var(--border)', paddingTop: '0.75rem', fontSize: '0.8rem', display: 'flex', justifyContent: 'space-between', color: 'var(--text-muted)', fontWeight: 600 }}>
                          <span>Feature Progress:</span>
                          <span style={{ color: 'var(--text-main)', fontFamily: 'var(--font-mono)' }}>
                            {scanDetails.perf_data.feature_progress}
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
 
                {/* Retro Terminal Logs */}
                <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                  <h3 style={{ fontFamily: 'var(--font-title)', fontSize: '1.1rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <Terminal size={18} style={{ color: 'var(--primary)' }} />
                    Live Agent Terminal Console
                  </h3>
                  <div className="log-panel" ref={logPanelContainerRef}>
                    {scanDetails.progress_log.map((log, i) => {
                      let type = 'info';
                      if (log.includes('[START]') || log.includes('[SUCCESS]')) type = 'success';
                      if (log.includes('[WARNING]')) type = 'warning';
                      if (log.includes('[ERROR]')) type = 'error';
                      return (
                        <div key={i} className={`log-line ${type}`}>
                          {log}
                        </div>
                      );
                    })}
                    {/* Live Line in CLI logs */}
                    {scanDetails.status === 'running' && scanDetails.perf_data && (
                      <div className="log-line info" style={{ color: '#60a5fa', fontWeight: 'bold' }}>
                        &gt; [{scanDetails.perf_data.stage || 'Initializing'}]
                        {scanDetails.perf_data.feature ? ` ${scanDetails.perf_data.feature}` : ''}
                        {scanDetails.perf_data.operation ? ` -> ${scanDetails.perf_data.operation}` : ''}
                        {scanDetails.perf_data.llm_stage ? ` [LLM: ${scanDetails.perf_data.llm_stage}]` : ''}
                        {" - 🟢 RUNNING - Elapsed: "}{formatTime(liveTimers.stageElapsed)}
                      </div>
                    )}
                  </div>
                </div>
 
              </div>
            )}

            {/* If Completed or Crawled Data exists, render the core workspace tab dashboard */}
            {(scanDetails.app_map.nodes?.length > 0 || scanDetails.test_cases?.length > 0) && (
              <div>
                {/* Final Performance Summary Break down */}
                {scanDetails.status === 'completed' && scanDetails.perf_data?.final_summary && (
                  <div className="glass-card" style={{ marginBottom: '2.5rem', padding: '1.5rem', border: '1px solid rgba(16, 185, 129, 0.2)', background: 'rgba(17, 17, 24, 0.85)' }}>
                    <h3 style={{ fontFamily: 'var(--font-title)', fontSize: '1.2rem', fontWeight: 800, marginBottom: '1.25rem', borderBottom: '1px solid var(--border)', paddingBottom: '0.5rem', color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      📊 Scan Performance Analysis
                    </h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1.5rem', marginBottom: '1.5rem' }}>
                      <div>
                        <div style={{ fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase' }}>Scope & Selected Module</div>
                        <div style={{ fontSize: '1.05rem', fontWeight: 800, color: 'var(--primary)', textTransform: 'uppercase', marginTop: '4px' }}>
                          {scanDetails.perf_data?.scan_scope || 'ENTIRE_APPLICATION'}
                        </div>
                        <div style={{ fontSize: '0.8rem', color: 'var(--text-main)', marginTop: '4px', fontWeight: 500 }}>
                          {scanDetails.description ? scanDetails.description.split('.')[0] : 'Entire App'}
                        </div>
                      </div>
                      
                      <div>
                        <div style={{ fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase' }}>Total Scan Time</div>
                        <div style={{ fontSize: '1.5rem', fontWeight: 900, color: '#34d399', fontFamily: 'var(--font-mono)', marginTop: '4px' }}>
                          {formatTime(Math.floor(scanDetails.perf_data.final_summary.scan?.duration || 0))}
                        </div>
                      </div>
                      
                      <div>
                        <div style={{ fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase' }}>Gemini AI Processing</div>
                        <div style={{ fontSize: '1.05rem', fontWeight: 800, color: '#fb923c', marginTop: '4px' }}>
                          {scanDetails.perf_data.final_summary.llm?.calls || 0} LLM calls
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '2px' }}>
                          Total Duration: {formatTime(Math.floor(scanDetails.perf_data.final_summary.llm?.duration || 0))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Stats Counters */}
                <div className="stats-row">
                  <div className="stat-card">
                    <div className="stat-icon-wrapper" style={{ background: 'rgba(79, 70, 229, 0.12)', color: '#818cf8' }}>
                      <Network size={20} />
                    </div>
                    <div>
                      <div className="stat-value">{scanDetails.app_map.nodes?.length || 0}</div>
                      <div className="stat-label">Pages Visited</div>
                    </div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-icon-wrapper" style={{ background: 'rgba(124, 58, 237, 0.12)', color: '#a78bfa' }}>
                      <LayoutGrid size={20} />
                    </div>
                    <div>
                      <div className="stat-value">
                        {scanDetails.app_map.nodes?.reduce((acc, curr) => acc + (curr.elements?.forms?.length || 0), 0) || 0}
                      </div>
                      <div className="stat-label">Forms Found</div>
                    </div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-icon-wrapper" style={{ background: 'rgba(20, 184, 166, 0.12)', color: '#2dd4bf' }}>
                      <FileText size={20} />
                    </div>
                    <div>
                      <div className="stat-value">{scanDetails.test_cases?.length || 0}</div>
                      <div className="stat-label">Test Cases</div>
                    </div>
                  </div>
                  <div className="stat-card" style={{ background: 'rgba(16, 185, 129, 0.06)', borderColor: 'rgba(16, 185, 129, 0.2)' }}>
                    <div className="stat-icon-wrapper success" style={{ background: 'rgba(16, 185, 129, 0.12)', color: '#34d399' }}>
                      <Bot size={20} />
                    </div>
                    <div>
                      <div className="stat-value" style={{ fontSize: '1.2rem', textTransform: 'uppercase', color: '#34d399' }}>
                        {scanDetails.status === 'running' ? 'RUNNING' : scanDetails.status === 'failed' ? 'FAILED' : 'COMPLETED'}
                      </div>
                      <div className="stat-label" style={{ color: 'rgba(16, 185, 129, 0.7)' }}>Agent State</div>
                    </div>
                  </div>
                </div>

                {/* Sub Tab Buttons */}
                <div className="tabs-header">
                  <button 
                    className={`tab-btn ${resultsTab === 'map' ? 'active' : ''}`}
                    onClick={() => setResultsTab('map')}
                  >
                    <Network size={16} />
                    Application Map
                  </button>
                  <button 
                    className={`tab-btn ${resultsTab === 'pages' ? 'active' : ''}`}
                    onClick={() => setResultsTab('pages')}
                  >
                    <LayoutGrid size={16} />
                    Discovered Layouts
                  </button>
                  <button 
                    className={`tab-btn ${resultsTab === 'testcases' ? 'active' : ''}`}
                    onClick={() => setResultsTab('testcases')}
                  >
                    <FileText size={16} />
                    Generated Test Cases
                  </button>
                  <button 
                    className={`tab-btn ${resultsTab === 'logs' ? 'active' : ''}`}
                    onClick={() => setResultsTab('logs')}
                  >
                    <Terminal size={16} />
                    Console Logs
                  </button>
                </div>

                {/* Sub Tab Outputs */}
                
                {/* 1. App Map Tab */}
                {resultsTab === 'map' && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '2rem', ...((selectedPageNode) ? { gridTemplateColumns: '1fr 320px' } : {}) }}>
                    
                    {/* SVG canvas */}
                    <CrawlerMap 
                      appMap={scanDetails.app_map} 
                      onNodeSelect={setSelectedPageNode}
                    />

                    {/* Element metadata sidebar drawer */}
                    {selectedPageNode && (
                      <div className="glass-card" style={{ padding: '1.25rem', fontSize: '0.85rem', display: 'flex', flexDirection: 'column', gap: '1.25rem', overflowY: 'auto', maxHeight: '530px' }}>
                        <div style={{ display: 'flex', justifyItems: 'center', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '0.5rem' }}>
                          <h4 style={{ fontFamily: 'var(--font-title)', fontSize: '0.95rem', fontWeight: 700 }}>Page Details</h4>
                          <button onClick={() => setSelectedPageNode(null)} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}>
                            <X size={16} />
                          </button>
                        </div>

                        {selectedPageNode.screenshot && (
                          <div style={{ position: 'relative' }}>
                            <img
                              src={resolveAssetUrl(selectedPageNode.screenshot)}
                              alt="Screenshot"
                              style={{ width: '100%', height: '110px', objectFit: 'cover', borderRadius: '8px', border: '1px solid var(--border)', cursor: 'zoom-in' }}
                              onClick={() => setLightboxImg(selectedPageNode.screenshot)}
                            />
                            <div style={{ position: 'absolute', bottom: '8px', right: '8px', background: 'rgba(0,0,0,0.6)', padding: '2px 6px', borderRadius: '4px', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.65rem' }}>
                              <Eye size={10} /> Zoom
                            </div>
                          </div>
                        )}

                        <div>
                          <h3 style={{ fontFamily: 'var(--font-title)', fontWeight: 800, fontSize: '1.1rem', marginBottom: '0.25rem' }}>
                            {selectedPageNode.label || 'Page Details'}
                          </h3>
                          <div style={{ fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase', marginTop: '0.75rem' }}>URL</div>
                          <a href={selectedPageNode.url} target="_blank" rel="noreferrer" style={{ wordBreak: 'break-all', color: 'var(--primary)', textDecoration: 'none', fontSize: '0.75rem', fontFamily: 'var(--font-mono)' }}>
                            {selectedPageNode.url}
                          </a>
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)', paddingTop: '0.75rem', paddingBottom: '0.75rem' }}>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: '1rem', fontWeight: 'bold' }}>{selectedPageNode.elements?.forms?.length || 0}</div>
                            <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Forms</div>
                          </div>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: '1rem', fontWeight: 'bold' }}>{selectedPageNode.elements?.buttons?.length || 0}</div>
                            <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Buttons</div>
                          </div>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: '1rem', fontWeight: 'bold' }}>{selectedPageNode.elements?.selects?.length || 0}</div>
                            <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Selectors</div>
                          </div>
                        </div>

                        <div>
                          <div style={{ fontWeight: 600, color: 'var(--text-muted)', fontSize: '0.75rem', textTransform: 'uppercase', marginBottom: '0.5rem' }}>
                            Discovered Elements
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', maxHeight: '180px', overflowY: 'auto', paddingRight: '4px' }}>
                            {(() => {
                              const list = [];
                              const el = selectedPageNode.elements || {};
                              
                              if (el.forms) {
                                el.forms.forEach(f => {
                                  f.fields?.forEach(fld => {
                                    if (fld.name) list.push(`Input field: "${fld.name}" (${fld.type})`);
                                  });
                                });
                              }
                              if (el.buttons) {
                                el.buttons.forEach(b => {
                                  if (b.text) list.push(`Click button: "${b.text}"`);
                                });
                              }
                              if (el.selects) {
                                el.selects.forEach(s => {
                                  if (s.name) list.push(`Dropdown: "${s.name}"`);
                                });
                              }
                              if (el.tables) {
                                el.tables.forEach(t => {
                                  if (t.headers && t.headers.length > 0) {
                                    list.push(`Columns: ${t.headers.join(', ')}`);
                                  }
                                });
                              }

                              if (list.length === 0) {
                                return <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', fontStyle: 'italic' }}>No interactive forms or buttons found on this page.</div>;
                              }

                              return list.map((item, idx) => (
                                <div key={idx} style={{ display: 'flex', gap: '0.35rem', alignItems: 'flex-start', color: 'var(--text-main)', fontSize: '0.75rem' }}>
                                  <ChevronRight size={12} style={{ color: 'var(--primary)', flexShrink: 0, marginTop: '2px' }} />
                                  <span>{item}</span>
                                </div>
                              ));
                            })()}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* 2. Discovered Pages Grid */}
                {resultsTab === 'pages' && (
                  <div className="pages-grid">
                    {(scanDetails?.app_map?.nodes || []).map(node => (
                      <div key={node.id} className="page-detail-card">
                        {node.screenshot ? (
                          <img
                            src={resolveAssetUrl(node.screenshot)}
                            alt={node.label}
                            className="page-screenshot-preview"
                            onClick={() => setLightboxImg(node.screenshot)}
                          />
                        ) : (
                          <div style={{ height: '180px', background: '#000', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
                            No Screenshot Available
                          </div>
                        )}
                        <div className="page-card-body">
                          <h4 className="page-card-title">{node.label}</h4>
                          <span className="page-card-url">{node.url}</span>
                          
                          <div className="elements-summary">
                            <span className="element-pill">Forms: {node.elements?.forms?.length || 0}</span>
                            <span className="element-pill">Buttons: {node.elements?.buttons?.length || 0}</span>
                            <span className="element-pill">Dropdowns: {node.elements?.selects?.length || 0}</span>
                            <span className="element-pill">Tables: {node.elements?.tables?.length || 0}</span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* 3. Generated Test Cases Tab */}
                {resultsTab === 'testcases' && (
                  <div className="glass-card">
                    {scanDetails.test_cases?.length === 0 ? (
                      <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                        Test suite is compiling. Generating test scenarios.
                      </div>
                    ) : (
                      <TestCaseTable 
                        testCases={scanDetails.test_cases} 
                        scanId={scanDetails.id}
                        excelPath={scanDetails.excel_path}
                      />
                    )}
                  </div>
                )}

                {/* 4. Logs Console Tab */}
                {resultsTab === 'logs' && (
                  <div className="glass-card">
                    <div className="log-panel" style={{ maxHeight: '550px' }}>
                      {scanDetails.progress_log.map((log, i) => {
                        let type = 'info';
                        if (log.includes('[START]') || log.includes('[SUCCESS]')) type = 'success';
                        if (log.includes('[WARNING]')) type = 'warning';
                        if (log.includes('[ERROR]')) type = 'error';
                        return (
                          <div key={i} className={`log-line ${type}`}>
                            {log}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Second Bottom Stats Counter */}
                <div className="bottom-stats-grid">
                  <div className="stat-card">
                    <div className="stat-icon-wrapper" style={{ background: 'rgba(59, 130, 246, 0.12)', color: '#60a5fa' }}>
                      <Network size={20} />
                    </div>
                    <div>
                      <div className="stat-value">{scanDetails.app_map.nodes?.length || 0}</div>
                      <div className="stat-label">Total Pages</div>
                    </div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-icon-wrapper" style={{ background: 'rgba(124, 58, 237, 0.12)', color: '#a78bfa' }}>
                      <LayoutGrid size={20} />
                    </div>
                    <div>
                      <div className="stat-value">
                        {scanDetails.app_map.nodes?.reduce((acc, curr) => acc + (curr.elements?.forms?.length || 0), 0) || 0}
                      </div>
                      <div className="stat-label">Total Forms</div>
                    </div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-icon-wrapper success" style={{ background: 'rgba(16, 185, 129, 0.12)', color: '#34d399' }}>
                      <FileText size={20} />
                    </div>
                    <div>
                      <div className="stat-value">{scanDetails.test_cases?.length || 0}</div>
                      <div className="stat-label">Total Test Cases</div>
                    </div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-icon-wrapper" style={{ background: 'rgba(249, 115, 22, 0.12)', color: '#fb923c' }}>
                      <Download size={20} />
                    </div>
                    <div>
                      <div className="stat-value">
                        {scanDetails.app_map.nodes?.reduce((acc, curr) => {
                          const buttons = curr.elements?.buttons?.length || 0;
                          const links = scanDetails.app_map.edges?.filter(e => e.source === curr.id).length || 0;
                          return acc + buttons + links;
                        }, 0) || 15}
                      </div>
                      <div className="stat-label">Total Links</div>
                    </div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-icon-wrapper" style={{ background: 'rgba(244, 63, 94, 0.12)', color: '#fb7185' }}>
                      <Folders size={20} />
                    </div>
                    <div>
                      <div className="stat-value">
                        {(() => {
                          const nodes = scanDetails.app_map.nodes || [];
                          const edges = scanDetails.app_map.edges || [];
                          if (nodes.length === 0) return 0;
                          const rootId = nodes[0].id;
                          const firstLevel = edges.filter(e => e.source === rootId).map(e => e.target);
                          return new Set(firstLevel).size || 6;
                        })()}
                      </div>
                      <div className="stat-label">Modules</div>
                    </div>
                  </div>
                </div>

              </div>
            )}
          </>
        )}

      </main>

      {/* 3. Lightbox Screenshot Overlay */}
      {lightboxImg && (
        <div className="modal-overlay" onClick={() => setLightboxImg(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close-btn" onClick={() => setLightboxImg(null)}>
              <X size={24} />
            </button>
            <img src={resolveAssetUrl(lightboxImg)} alt="Page Layout Screenshot Zoom" className="modal-image" />
          </div>
        </div>
      )}

    </div>
  );
}
