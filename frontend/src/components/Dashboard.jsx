import React, {useState} from 'react';
import {Play, Globe, Shield, FileText, ChevronDown, ChevronUp, RotateCcw, Bot} from 'lucide-react';
import {DEFAULT_AI_INSTRUCTIONS, MODULE_WISE_AI_INSTRUCTIONS} from '../constants/prompts';

const isValidUrl = (value) => {
    const trimmed = value.trim();
    if (!trimmed) return false;

    let testUrl = trimmed;
    if (!/^https?:\/\//i.test(testUrl)) {
        testUrl = 'http://' + testUrl;
    }

    try {
        const urlObj = new URL(testUrl);
        const hostname = urlObj.hostname;
        if (!hostname) return false;

        if (hostname.toLowerCase() === 'localhost') return true;

        // Hostname regex validation (must contain a dot, and only valid characters)
        const hostRegex = /^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
        // Also support ip addresses
        const ipRegex = /^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/;

        return hostRegex.test(hostname) || ipRegex.test(hostname);
    } catch (e) {
        return false;
    }
};

export default function Dashboard({onSubmit, loading}) {
    const [url, setUrl] = useState('');
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [description, setDescription] = useState('');
    const [showAuth, setShowAuth] = useState(false);
    const [hasUserEditedInstructions, setHasUserEditedInstructions] = useState(false);
    const [apiKey, setApiKey] = useState('');
    const [modelName, setModelName] = useState('gemini-1.5-flash');
    const [showGemini, setShowGemini] = useState(false);
    const [scanScope, setScanScope] = useState('');
    const [parentModule, setParentModule] = useState('');
    const [selectedModule, setSelectedModule] = useState('');

    const handleUrlChange = (e) => {
        const val = e.target.value;
        setUrl(val);

        if (!isValidUrl(val)) {
            setScanScope('');
            if (!hasUserEditedInstructions) {
                setDescription('');
            }
        } else {
            if (scanScope && !hasUserEditedInstructions && !description.trim()) {
                setDescription(scanScope === 'entire' ? DEFAULT_AI_INSTRUCTIONS : MODULE_WISE_AI_INSTRUCTIONS);
            }
        }
    };

    const handleUrlBlur = () => {
        if (!isValidUrl(url)) {
            setScanScope('');
            if (!hasUserEditedInstructions) {
                setDescription('');
            }
        } else {
            if (scanScope && !hasUserEditedInstructions && !description.trim()) {
                setDescription(scanScope === 'entire' ? DEFAULT_AI_INSTRUCTIONS : MODULE_WISE_AI_INSTRUCTIONS);
            }
        }
    };

    const handleScopeChange = (e) => {
        const scope = e.target.value;
        setScanScope(scope);

        if (!scope) {
            if (!hasUserEditedInstructions) {
                setDescription('');
            }
            return;
        }

        if (!hasUserEditedInstructions) {
            if (scope === 'entire') {
                setDescription(DEFAULT_AI_INSTRUCTIONS);
            } else if (scope === 'module') {
                setDescription(MODULE_WISE_AI_INSTRUCTIONS);
            }
        }
    };

    const handleDescriptionChange = (e) => {
        setDescription(e.target.value);
        setHasUserEditedInstructions(true);
    };

    const handleReset = () => {
        if (scanScope === 'entire') {
            setDescription(DEFAULT_AI_INSTRUCTIONS);
        } else if (scanScope === 'module') {
            setDescription(MODULE_WISE_AI_INSTRUCTIONS);
        }
        setHasUserEditedInstructions(false);
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!url) return;

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
            api_key: apiKey.trim() || null,
            model_name: modelName || null,
            scan_scope: scanScope || null,
            parent_module: scanScope === 'module' ? parentModule.trim() : null,
            selected_module: scanScope === 'module' ? selectedModule.trim() : null,
        });
    };

    const getExpectedDefaultInstructions = () => {
        if (scanScope === 'entire') return DEFAULT_AI_INSTRUCTIONS;
        if (scanScope === 'module') return MODULE_WISE_AI_INSTRUCTIONS;
        return '';
    };

    const isResetDisabled = description === getExpectedDefaultInstructions();

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
                        onChange={handleUrlChange}
                        onBlur={handleUrlBlur}
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

                {/* Gemini Settings Toggler */}
                <div className="form-group span-2">
                    <div className="auth-header-toggle" onClick={() => setShowGemini(!showGemini)}>
                        <Bot className="menu-icon"
                             style={{color: showGemini ? 'var(--secondary)' : 'var(--text-muted)'}}/>
                        <span>Gemini AI Settings (Optional)</span>
                        {showGemini ? <ChevronUp size={16}/> : <ChevronDown size={16}/>}
                    </div>

                    {showGemini && (
                        <div className="auth-block">
                            <div className="form-group">
                                <label className="form-label" htmlFor="api-key-input">Gemini API Key</label>
                                <input
                                    id="api-key-input"
                                    type="password"
                                    className="form-input"
                                    placeholder="AIzaSy..."
                                    value={apiKey}
                                    onChange={(e) => setApiKey(e.target.value)}
                                    disabled={loading}
                                />
                            </div>
                            <div className="form-group">
                                <label className="form-label" htmlFor="model-select">Gemini Model</label>
                                <input
                                    id="gemini-model-id"
                                    type="text"
                                    className="form-input"
                                    placeholder="Enter Gemini model name - eg : gemini-3.1-flash-lite"
                                    value={modelName}
                                    onChange={(e) => setModelName(e.target.value.trim())}
                                    disabled={loading}
                                    autoComplete="off"
                                />
                            </div>
                        </div>
                    )}
                </div>

                {/* Scan Scope Dropdown (shown only when URL is valid) */}
                {isValidUrl(url) && (
                    <div className="form-group span-2" style={{animation: 'slideDown 0.3s forwards'}}>
                        <label className="form-label" htmlFor="scope-select">
                            Select Scan Scope
                        </label>
                        <select
                            id="scope-select"
                            className="form-input"
                            value={scanScope}
                            onChange={handleScopeChange}
                            disabled={loading}
                            style={{background: 'rgba(17, 17, 24, 0.95)', color: 'var(--text-main)', cursor: 'pointer'}}
                        >
                            <option value="" disabled>Select scan scope...</option>
                            <option value="entire">Entire Application</option>
                            <option value="module">Module Wise</option>
                        </select>
                    </div>
                )}

                {/* Parent and Target module inputs */}
                {isValidUrl(url) && scanScope === 'module' && (
                    <>
                        <div className="form-group" style={{animation: 'slideDown 0.3s forwards'}}>
                            <label className="form-label" htmlFor="parent-module-input">
                                Parent Module
                            </label>
                            <input
                                id="parent-module-input"
                                type="text"
                                required
                                className="form-input"
                                placeholder="e.g. Manage"
                                value={parentModule}
                                onChange={(e) => setParentModule(e.target.value)}
                                disabled={loading}
                            />
                        </div>
                        <div className="form-group" style={{animation: 'slideDown 0.3s forwards'}}>
                            <label className="form-label" htmlFor="selected-module-input">
                                Target Module
                            </label>
                            <input
                                id="selected-module-input"
                                type="text"
                                required
                                className="form-input"
                                placeholder="e.g. Clients"
                                value={selectedModule}
                                onChange={(e) => setSelectedModule(e.target.value)}
                                disabled={loading}
                            />
                        </div>
                    </>
                )}

                {/* Additional instructions (shown only when URL is valid and scope is selected) */}
                {isValidUrl(url) && scanScope && (
                    <div className="form-group span-2" style={{animation: 'slideDown 0.3s forwards'}}>
                        <div style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            marginBottom: '4px'
                        }}>
                            <label className="form-label" htmlFor="desc-input" style={{margin: 0}}>
                                <FileText className="menu-icon" style={{
                                    verticalAlign: 'middle',
                                    marginRight: '6px',
                                    color: 'var(--primary)'
                                }}/>
                                AI Instructions
                            </label>
                            <button
                                type="button"
                                className="reset-btn"
                                onClick={handleReset}
                                disabled={isResetDisabled}
                                title="Reset to Default Prompt"
                            >
                                <RotateCcw size={12}/>
                                Reset to Default
                            </button>
                        </div>
                        <p className="helper-text">
                            We've added a default QA analysis prompt. You can edit it to customize what the AI should
                            test.
                        </p>
                        <textarea
                            id="desc-input"
                            rows="8"
                            className="form-input"
                            placeholder="This is an Employee Management System. Focus on CRUD operations, pagination validation, search bars, filters, and login workflows."
                            value={description}
                            onChange={handleDescriptionChange}
                            disabled={loading}
                            style={{resize: 'vertical', lineHeight: '1.5'}}
                        />
                    </div>
                )}

                {/* Form Action */}
                <div className="span-2" style={{textAlign: 'right', marginTop: '1rem'}}>
                    <button type="submit" className="primary-btn" disabled={loading || !url}>
                        <Play size={18} fill="currentColor"/>
                        {loading ? 'Initializing Agent...' : 'Launch Automated QA Agent'}
                    </button>
                </div>

            </form>
        </div>
    );
}

