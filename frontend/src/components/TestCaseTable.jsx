import React, { useState } from 'react';
import { Download, Search, ChevronRight, ChevronDown, CheckCircle2 } from 'lucide-react';
import { API_BASE_URL } from '../api';

export default function TestCaseTable({ testCases, scanId, excelPath }) {
  const [searchQuery, setSearchQuery] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('ALL');
  const [typeFilter, setTypeFilter] = useState('ALL');
  const [expandedRows, setExpandedRows] = useState({});

  const toggleRow = (id) => {
    setExpandedRows(prev => ({
      ...prev,
      [id]: !prev[id]
    }));
  };

  const handleDownload = () => {
    if (!scanId) return;
    window.open(`${API_BASE_URL}/api/scan/${scanId}/download`, '_blank');
  };

  // 1. Run Filters
  const filteredCases = testCases.filter(tc => {
    const matchesSearch = 
      (tc.scenario || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (tc.module || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (tc.test_case_id || '').toLowerCase().includes(searchQuery.toLowerCase());
      
    const matchesPriority = 
      priorityFilter === 'ALL' || 
      (tc.priority || '').toUpperCase().trim() === priorityFilter;

    const matchesType = 
      typeFilter === 'ALL' || 
      (tc.test_type || '').toUpperCase().trim() === typeFilter;

    return matchesSearch && matchesPriority && matchesType;
  });

  // Extract unique test types for filters
  const testTypes = ['ALL', ...new Set(testCases.map(tc => (tc.test_type || '').toUpperCase().trim()).filter(Boolean))];

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        
        {/* Search and Filters */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', flex: 1, maxWidth: '750px' }}>
          
          {/* Search bar */}
          <div style={{ position: 'relative', flex: 1, minWidth: '220px' }}>
            <Search size={18} style={{ position: 'absolute', left: '12px', top: '12px', color: 'var(--text-muted)' }} />
            <input
              type="text"
              className="form-input"
              style={{ paddingLeft: '2.5rem', width: '100%' }}
              placeholder="Search scenarios, modules..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          {/* Priority filter */}
          <select 
            className="form-input" 
            style={{ minWidth: '130px', cursor: 'pointer' }}
            value={priorityFilter}
            onChange={(e) => setPriorityFilter(e.target.value)}
          >
            <option value="ALL">Priority: All</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>

          {/* Type filter */}
          <select 
            className="form-input" 
            style={{ minWidth: '150px', cursor: 'pointer' }}
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            {testTypes.map(t => (
              <option key={t} value={t}>{t === 'ALL' ? 'Type: All' : t.charAt(0) + t.slice(1).toLowerCase()}</option>
            ))}
          </select>
        </div>

        {/* Download Button */}
        {excelPath && (
          <button onClick={handleDownload} className="primary-btn" style={{ padding: '0.7rem 1.3rem', fontSize: '0.9rem' }}>
            <Download size={16} />
            Download Excel Suite
          </button>
        )}
      </div>

      {filteredCases.length === 0 ? (
        <div className="glass-card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
          No test cases match the active filter criteria.
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="custom-table">
            <thead>
              <tr>
                <th style={{ width: '40px' }}></th>
                <th style={{ width: '150px' }}>ID</th>
                <th style={{ width: '180px' }}>Module</th>
                <th>Scenario Description</th>
                <th style={{ width: '100px', textAlign: 'center' }}>Priority</th>
                <th style={{ width: '130px', textAlign: 'center' }}>Test Type</th>
              </tr>
            </thead>
            <tbody>
              {filteredCases.map(tc => {
                const rowId = tc.test_case_id;
                const isExpanded = !!expandedRows[rowId];
                const priorityClass = (tc.priority || 'medium').toLowerCase().trim();
                
                return (
                  <React.Fragment key={rowId}>
                    
                    {/* Collapsible Trigger Row */}
                    <tr onClick={() => toggleRow(rowId)} style={{ cursor: 'pointer' }}>
                      <td style={{ verticalAlign: 'middle', textAlign: 'center' }}>
                        {isExpanded ? <ChevronDown size={16} className="text-muted" /> : <ChevronRight size={16} className="text-muted" />}
                      </td>
                      <td style={{ verticalAlign: 'middle', fontWeight: 600, color: 'var(--primary)' }}>
                        {tc.test_case_id}
                      </td>
                      <td style={{ verticalAlign: 'middle', fontWeight: 500 }}>
                        {tc.module}
                      </td>
                      <td style={{ verticalAlign: 'middle', fontWeight: 500 }}>
                        {tc.scenario}
                      </td>
                      <td style={{ verticalAlign: 'middle', textAlign: 'center' }}>
                        <span className={`badge ${priorityClass}`}>{tc.priority}</span>
                      </td>
                      <td style={{ verticalAlign: 'middle', textAlign: 'center' }}>
                        <span className="element-pill" style={{ color: 'var(--text-main)', border: 'none', background: 'rgba(255,255,255,0.06)' }}>
                          {tc.test_type}
                        </span>
                      </td>
                    </tr>

                    {/* Collapsed Details Row */}
                    {isExpanded && (
                      <tr style={{ background: 'rgba(255,255,255,0.015)' }}>
                        <td colSpan="6" style={{ padding: '1.25rem 2rem', borderBottom: '1px solid var(--border)' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1.25rem' }}>
                            
                            {/* Preconditions */}
                            {tc.preconditions && (
                              <div>
                                <h5 style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.35rem', fontWeight: 700 }}>
                                  Preconditions
                                </h5>
                                <p style={{ fontSize: '0.875rem', color: 'var(--text-main)' }}>
                                  {tc.preconditions}
                                </p>
                              </div>
                            )}

                            {/* Test Steps */}
                            <div>
                              <h5 style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.35rem', fontWeight: 700 }}>
                                Test Execution Steps
                              </h5>
                              <p style={{ fontSize: '0.875rem', color: 'var(--text-main)', whiteSpace: 'pre-wrap', lineHeight: '1.6' }}>
                                {tc.test_steps}
                              </p>
                            </div>

                            {/* Expected Result */}
                            <div>
                              <h5 style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.35rem', fontWeight: 700 }}>
                                Expected Outcome
                              </h5>
                              <p style={{ fontSize: '0.875rem', color: 'var(--accent)', fontWeight: 500 }}>
                                {tc.expected_result}
                              </p>
                            </div>

                          </div>
                        </td>
                      </tr>
                    )}

                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
