import React from 'react';

// Last-resort safety net: React error boundaries can only be class components (no hook
// equivalent). Without this, any unexpected render-time exception anywhere in the tree
// unmounts the entire app, leaving a blank page with no indication of what happened.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('Unhandled UI error:', error, info?.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'var(--bg-main, #0b0b10)', color: 'var(--text-main, #f5f5f7)', padding: '2rem'
        }}>
          <div className="glass-card" style={{ maxWidth: '480px', padding: '2rem', textAlign: 'center' }}>
            <h2 style={{ marginBottom: '0.75rem' }}>Something went wrong</h2>
            <p style={{ color: 'var(--text-muted, #9ca3af)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
              The workspace hit an unexpected error and couldn't render. Reloading usually resolves this.
            </p>
            <button className="primary-btn" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
