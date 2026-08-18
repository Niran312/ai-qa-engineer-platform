import React, { useState } from 'react';
import { History, Search, Link2, Calendar, FileCheck, AlertCircle, Loader } from 'lucide-react';

export default function HistoryList({ scans, activeScanId, onSelectScan }) {
  const [searchTerm, setSearchTerm] = useState('');

  const filteredScans = scans.filter(scan => 
    (scan.url || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getStatusIcon = (status) => {
    switch (status.toLowerCase()) {
      case 'completed':
        return <FileCheck size={14} style={{ color: 'var(--accent)' }} />;
      case 'failed':
        return <AlertCircle size={14} style={{ color: 'var(--danger)' }} />;
      case 'running':
        return <Loader size={14} className="spin" style={{ color: 'var(--primary)' }} />;
      default:
        return <Loader size={14} style={{ color: 'var(--text-muted)' }} />;
    }
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '';
    try {
      const date = new Date(dateStr.replace(' ', 'T')); // Convert SQLite standard to iso format
      return date.toLocaleDateString(undefined, { 
        month: 'short', 
        day: 'numeric', 
        hour: '2-digit', 
        minute: '2-digit' 
      });
    } catch (e) {
      return dateStr;
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', height: '100%' }}>
      
      {/* Title */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-main)', borderBottom: '1px solid var(--border)', paddingBottom: '0.75rem' }}>
        <History size={18} style={{ color: 'var(--primary)' }} />
        <span style={{ fontSize: '0.9rem', fontWeight: 600, fontFamily: 'var(--font-title)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Scan History
        </span>
      </div>

      {/* Search Input */}
      <div style={{ position: 'relative' }}>
        <Search size={14} style={{ position: 'absolute', left: '10px', top: '10px', color: 'var(--text-muted)' }} />
        <input
          type="text"
          className="form-input"
          style={{ paddingLeft: '2rem', paddingRight: '0.5rem', paddingTop: '0.45rem', paddingBottom: '0.45rem', fontSize: '0.8rem', width: '100%' }}
          placeholder="Filter history..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>

      {/* History Items List */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', overflowY: 'auto', flex: 1, maxHeight: 'calc(100vh - 250px)', paddingRight: '2px' }}>
        {filteredScans.length === 0 ? (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem', textAlign: 'center', padding: '1rem' }}>
            {searchTerm ? 'No matches' : 'No scans yet'}
          </span>
        ) : (
          filteredScans.map(scan => {
            const isActive = scan.id === activeScanId;
            const displayUrl = scan.url.replace(/^https?:\/\/(www\.)?/, '');
            
            return (
              <div
                key={scan.id}
                onClick={() => onSelectScan(scan.id)}
                style={{
                  background: isActive ? 'var(--primary-glow)' : 'rgba(255,255,255,0.02)',
                  border: isActive ? '1px solid rgba(79,70,229,0.3)' : '1px solid var(--border)',
                  borderRadius: '10px',
                  padding: '0.75rem',
                  cursor: 'pointer',
                  transition: 'all 0.2s',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.35rem'
                }}
                className={isActive ? 'history-active' : ''}
              >
                {/* URL row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', justifyContent: 'space-between' }}>
                  <span style={{ 
                    fontSize: '0.85rem', 
                    fontWeight: 600, 
                    color: isActive ? '#fff' : 'var(--text-main)', 
                    overflow: 'hidden', 
                    textOverflow: 'ellipsis', 
                    whiteSpace: 'nowrap',
                    maxWidth: '170px'
                  }}>
                    {displayUrl}
                  </span>
                  {getStatusIcon(scan.status)}
                </div>

                {/* Date & Details */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', justifyContent: 'space-between', color: 'var(--text-muted)', fontSize: '0.7rem' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.2rem' }}>
                    <Calendar size={10} />
                    {formatDate(scan.created_at)}
                  </span>
                  <span style={{ textTransform: 'capitalize', fontWeight: 500, color: scan.status === 'completed' ? 'var(--accent)' : scan.status === 'failed' ? 'var(--danger)' : 'var(--primary)' }}>
                    {scan.status}
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>

    </div>
  );
}
