import React, {useState, useEffect} from 'react';
import {Play, Globe, Shield, FileText, ChevronDown, ChevronUp, Bot} from 'lucide-react';
import {apiFetch} from '../api';

// Bring-your-own-key: the key's prefix decides which AI provider runs the scan.
const detectKeyProvider = (key) => {
    const k = key.trim();
    if (!k) return null;
    if (k.startsWith('sk-ant-')) return 'Anthropic (Claude Sonnet 5.5)';
    if (k.startsWith('sk-or-')) return 'OpenRouter';
    return 'unknown';
};

export default function Dashboard({onSubmit, loading}) {
    const [url, setUrl] = useState('');
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [description, setDescription] = useState('');
    const [showAuth, setShowAuth] = useState(false);
    const [showInstructions, setShowInstructions] = useState(false);
    const [apiKey, setApiKey] = useState('');
    const [showAiSettings, setShowAiSettings] = useState(true);
    // null = unknown yet; when the server has no key of its own, every user must bring one.
    const [serverKeyConfigured, setServerKeyConfigured] = useState(null);

    useEffect(() => {
        apiFetch('/api/config')
            .then((res) => (res.ok ? res.json() : null))
            .then((cfg) => setServerKeyConfigured(cfg ? !!cfg.server_key_configured : null))
            .catch(() => setServerKeyConfigured(null));
    }, []);

    const keyProvider = detectKeyProvider(apiKey);
    const keyRequired = serverKeyConfigured === false;
    const keyInvalid = keyProvider === 'unknown';
    const keyMissing = keyRequired && !apiKey.trim();

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!url || keyInvalid || keyMissing) return;

        // Add http:// prefix if user forgot it
        let cleanUrl = url.trim();
        if (!/^https?:\/\//i.test(cleanUrl)) {
            cleanUrl = 'http://' + cleanUrl;
        }

        onSubmit({
            url: cleanUrl,
            username: username.trim() || null,
            password: password || null,
            description: description.trim() || null,
            // Sent only with this request; never saved in the browser or on the server.
            api_key: apiKey.trim() || null,
        });
    };

    return (
        <div className="glass-card">
            <h2 className="brand-name" style={{fontSize: '1.5rem', marginBottom: '1.5rem'}}>
                Configure Website Scan
            </h2>
            <form onSubmit={handleSubmit} className="form-grid">

                {/* Website URL */}
                <div className="form-group span-2">
                    <label className="form-label" htmlFor="url-input">
                        <Globe className="menu-icon"
                               style={{verticalAlign: 'middle', marginRight: '6px', color: 'var(--primary)'}}/>
                        Target Website URL
                    </label>
                    <input
                        id="url-input"
                        type="text"
                        required
                        className="form-input"
                        placeholder="https://example.com"
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                        disabled={loading}
                    />
                </div>

                {/* Credentials toggler */}
                <div className="form-group span-2">
                    <div className="auth-header-toggle" onClick={() => setShowAuth(!showAuth)}>
                        <Shield className="menu-icon"
                                style={{color: showAuth ? 'var(--secondary)' : 'var(--text-muted)'}}/>
                        <span>Authentication Details (Optional)</span>
                        {showAuth ? <ChevronUp size={16}/> : <ChevronDown size={16}/>}
                    </div>

                    {showAuth && (
                        <div className="auth-block">
                            <div className="form-group">
                                <label className="form-label" htmlFor="username-input">Username</label>
                                <input
                                    id="username-input"
                                    type="text"
                                    className="form-input"
                                    placeholder="admin"
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label" htmlFor="password-input">Password</label>
                                <input
                                    id="password-input"
                                    type="password"
                                    className="form-input"
                                    placeholder="••••••••"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                        </div>
                    )}
                </div>

                {/* AI API Key (bring your own key) */}
                <div className="form-group span-2">
                    <div className="auth-header-toggle" onClick={() => setShowAiSettings(!showAiSettings)}>
                        <Bot className="menu-icon"
                             style={{color: showAiSettings ? 'var(--secondary)' : 'var(--text-muted)'}}/>
                        <span>AI API Key {keyRequired ? '(Required)' : '(Optional)'}</span>
                        {showAiSettings ? <ChevronUp size={16}/> : <ChevronDown size={16}/>}
                    </div>

                    {showAiSettings && (
                        <div className="auth-block">
                            <div className="form-group">
                                <label className="form-label" htmlFor="api-key-input">Your Anthropic or OpenRouter API Key</label>
                                <p className="helper-text">
                                    Used only for this scan and never stored. AI usage is billed to your own account.
                                    {serverKeyConfigured === true && ' Leave empty to use the server\'s key.'}
                                </p>
                                <input
                                    id="api-key-input"
                                    type="password"
                                    autoComplete="off"
                                    className="form-input"
                                    placeholder="sk-ant-...  or  sk-or-v1-..."
                                    value={apiKey}
                                    onChange={(e) => setApiKey(e.target.value)}
                                    disabled={loading}
                                    required={keyRequired}
                                    style={keyInvalid ? {borderColor: 'var(--danger)'} : undefined}
                                />
                                {keyProvider && !keyInvalid && (
                                    <p className="helper-text" style={{marginTop: '0.5rem', color: 'var(--accent)'}}>
                                        Provider: {keyProvider}
                                    </p>
                                )}
                                {keyInvalid && (
                                    <p className="helper-text" style={{marginTop: '0.5rem', color: 'var(--danger)'}}>
                                        Unrecognized key. Anthropic keys start with sk-ant-, OpenRouter keys with sk-or-.
                                    </p>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {/* Optional extra instructions - added on top of the server's default QA prompt */}
                <div className="form-group span-2">
                    <div className="auth-header-toggle" onClick={() => setShowInstructions(!showInstructions)}>
                        <FileText className="menu-icon"
                                  style={{color: showInstructions ? 'var(--secondary)' : 'var(--text-muted)'}}/>
                        <span>Extra Instructions (Optional)</span>
                        {showInstructions ? <ChevronUp size={16}/> : <ChevronDown size={16}/>}
                    </div>

                    {showInstructions && (
                        <div className="auth-block">
                            <div className="form-group">
                                <label className="form-label" htmlFor="desc-input">What should the AI focus on?</label>
                                <p className="helper-text">
                                    The AI already follows a detailed QA testing prompt. Anything you add here is
                                    applied on top of it.
                                </p>
                                <textarea
                                    id="desc-input"
                                    rows="4"
                                    className="form-input"
                                    placeholder="e.g. Focus on the checkout flow and form validation. Skip the blog pages."
                                    value={description}
                                    onChange={(e) => setDescription(e.target.value)}
                                    disabled={loading}
                                    style={{resize: 'vertical', lineHeight: '1.5'}}
                                />
                            </div>
                        </div>
                    )}
                </div>

                {/* Form Action */}
                <div className="span-2" style={{textAlign: 'right', marginTop: '1rem'}}>
                    <button type="submit" className="primary-btn" disabled={loading || !url || keyInvalid || keyMissing}>
                        <Play size={18} fill="currentColor"/>
                        {loading ? 'Initializing Agent...' : 'Launch Automated QA Agent'}
                    </button>
                </div>

            </form>
        </div>
    );
}

