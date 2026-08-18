import React, { useEffect, useState, useRef } from 'react';
import ReactFlow, { 
  MiniMap, 
  Background, 
  useNodesState, 
  useEdgesState, 
  MarkerType, 
  useReactFlow, 
  ReactFlowProvider, 
  Position,
  Handle
} from 'reactflow';
import dagre from 'dagre';
import { 
  ZoomIn, 
  ZoomOut, 
  Maximize2, 
  Minimize2, 
  Download, 
  RefreshCw, 
  Eye, 
  Folders,
  ChevronRight,
  ChevronDown
} from 'lucide-react';
import 'reactflow/dist/style.css';

// --- ICON EMOJI MAPPER MATCHING THE REFERENCE IMAGE ---
const getIconEmoji = (label, url, nodeType) => {
  const lbl = label.toLowerCase();
  const path = url.toLowerCase();
  
  if (path.includes('login') || path.includes('auth') || path.includes('signin')) return '🔑';
  if (lbl.includes('add') || lbl.includes('create') || lbl.includes('new')) return '➕';
  if (lbl.includes('edit') || lbl.includes('update') || lbl.includes('modify')) return '✏️';
  if (lbl.includes('details') || lbl.includes('view') || lbl.includes('info')) return '👁️';
  if (path.includes('dashboard') || lbl.includes('dashboard')) return '📊';
  if (path.includes('profile') || lbl.includes('profile')) return '👤';
  if (path.includes('security') || lbl.includes('security')) return '🔒';
  
  if (lbl.includes('users') || lbl.includes('employees') || lbl.includes('members')) return '👥';
  if (lbl.includes('projects') || lbl.includes('workspace') || lbl.includes('tasks')) return '📁';
  if (lbl.includes('reports') || lbl.includes('charts') || lbl.includes('analytics')) return '📊';
  if (lbl.includes('settings') || lbl.includes('config') || lbl.includes('options')) return '⚙️';
  
  if (nodeType === 'entry') return '🏠';
  if (nodeType === 'external') return '↗️';
  
  return '📄';
};

// --- CUSTOM NODE CARD DESIGN ---
const PageNodeCustom = ({ data }) => {
  const { 
    label, 
    url, 
    formsCount, 
    linksCount, 
    nodeType, 
    isCollapsed, 
    onToggleCollapse, 
    hasChildren 
  } = data;

  const emoji = getIconEmoji(label, url, nodeType);

  const getThemeClass = () => {
    switch (nodeType) {
      case 'entry': return 'entry-theme';
      case 'module': return 'module-theme';
      case 'external': return 'external-theme';
      default: return 'child-theme';
    }
  };

  const getRelativePath = () => {
    try {
      const parsed = new URL(url);
      return parsed.pathname + parsed.search;
    } catch(e) {
      // Handle relative paths
      if (url.startsWith('/')) return url;
      return '/' + url.split('/').slice(3).join('/');
    }
  };

  return (
    <div className={`page-node-card ${getThemeClass()}`}>
      {/* Invisible Handles to keep tree layout links perfectly straight */}
      <Handle type="target" position={Position.Top} style={{ background: 'transparent', border: 'none' }} />
      
      <div className="node-card-header">
        <span className="node-icon-emoji">{emoji}</span>
        <span className="node-title" title={label}>{label}</span>
      </div>

      <div className="node-path" title={url}>{getRelativePath()}</div>

      {nodeType === 'external' ? (
        <div className="node-stats">
          <span className="node-badge external-theme">External Link</span>
        </div>
      ) : (
        <div className="node-stats">
          {nodeType === 'entry' ? (
            <>
              <span className="node-badge entry-theme">Entry Point</span>
              <span className="node-badge entry-theme">{linksCount} Links</span>
            </>
          ) : (
            <>
              <span className={`node-badge ${getThemeClass()}`}>{formsCount} {formsCount === 1 ? 'Form' : 'Forms'}</span>
              <span className={`node-badge ${getThemeClass()}`}>{linksCount} {linksCount === 1 ? 'Link' : 'Links'}</span>
            </>
          )}
        </div>
      )}

      {hasChildren && (
        <button 
          className="node-collapse-toggle" 
          onClick={(e) => {
            e.stopPropagation();
            onToggleCollapse();
          }}
        >
          {isCollapsed ? <ChevronRight size={10} /> : <ChevronDown size={10} />}
        </button>
      )}

      <Handle type="source" position={Position.Bottom} style={{ background: 'transparent', border: 'none' }} />
    </div>
  );
};



const nodeTypes = {
  pageNode: PageNodeCustom
};

// --- DAGRE HIERARCHICAL LAYOUT SOLVER ---
const getLayoutedElements = (nodes, edges) => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  
  const nodeWidth = 240;
  const nodeHeight = 110; // Match more compact height in reference design
  
  dagreGraph.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 80 }); // Tighter separations to match image
  
  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: nodeWidth, height: nodeHeight });
  });
  
  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });
  
  dagre.layout(dagreGraph);
  
  const layoutedNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    return {
      ...node,
      targetPosition: Position.Top,
      sourcePosition: Position.Bottom,
      position: {
        x: nodeWithPosition.x - nodeWidth / 2,
        y: nodeWithPosition.y - nodeHeight / 2,
      },
    };
  });
  
  return { nodes: layoutedNodes, edges };
};

// --- MAIN GRAPH VIEWPORT ---
function CrawlerMapContent({ appMap, onNodeSelect }) {
  const { nodes: propNodes = [], edges: propEdges = [] } = appMap;
  
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [collapsedNodes, setCollapsedNodes] = useState(new Set());
  const [hoveredNode, setHoveredNode] = useState(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [isFullscreen, setIsFullscreen] = useState(false);
  
  const containerRef = useRef(null);
  const { zoomIn, zoomOut, fitView, setViewport } = useReactFlow();

  // Spanning tree construction to establish parent-child relationships and determine level
  const [treeStructure, setTreeStructure] = useState({ spanningTree: {}, parentMap: {}, levels: {} });

  useEffect(() => {
    if (!propNodes.length) return;

    const startUrl = propNodes[0].url;
    let startDomain = '';
    try {
      startDomain = new URL(startUrl).hostname;
    } catch(e) {}

    // Find root (usually incoming links = 0, fallback to first node)
    const incomingCount = {};
    propNodes.forEach(n => incomingCount[n.id] = 0);
    propEdges.forEach(e => {
      if (incomingCount[e.target] !== undefined) {
        incomingCount[e.target]++;
      }
    });

    let rootId = propNodes[0].id;
    for (const node of propNodes) {
      if (incomingCount[node.id] === 0) {
        rootId = node.id;
        break;
      }
    }

    // BFS to extract unique parent-child relationships (Spanning Tree)
    const spanningTree = {};
    const parentMap = {};
    const levels = {};
    propNodes.forEach(n => {
      spanningTree[n.id] = [];
      levels[n.id] = 0;
    });

    const visited = new Set([rootId]);
    const queue = [rootId];
    levels[rootId] = 0;

    const adjacency = {};
    propNodes.forEach(n => adjacency[n.id] = []);
    propEdges.forEach(e => {
      if (adjacency[e.source]) adjacency[e.source].push(e.target);
    });

    while (queue.length > 0) {
      const currId = queue.shift();
      const nextNodes = adjacency[currId] || [];
      
      nextNodes.forEach(nextId => {
        if (!visited.has(nextId) && adjacency[nextId] !== undefined) {
          visited.add(nextId);
          spanningTree[currId].push(nextId);
          parentMap[nextId] = currId;
          levels[nextId] = levels[currId] + 1;
          queue.push(nextId);
        }
      });
    }

    // Handle orphans
    propNodes.forEach(n => {
      if (!visited.has(n.id)) {
        visited.add(n.id);
        spanningTree[rootId].push(n.id);
        parentMap[n.id] = rootId;
        levels[n.id] = 1;
      }
    });

    setTreeStructure({ spanningTree, parentMap, levels });
  }, [propNodes, propEdges]);

  // Handle visibility filtering & dagre positioning
  useEffect(() => {
    if (!propNodes.length || !treeStructure.levels) return;

    const isNodeVisible = (nodeId) => {
      let parentId = treeStructure.parentMap[nodeId];
      while (parentId) {
        if (collapsedNodes.has(parentId)) {
          return false;
        }
        parentId = treeStructure.parentMap[parentId];
      }
      return true;
    };

    const startUrl = propNodes[0].url;
    let startDomain = '';
    try {
      startDomain = new URL(startUrl).hostname;
    } catch(e) {}

    // Map raw node objects to React Flow node format
    const visibleNodesRaw = propNodes.filter(node => isNodeVisible(node.id));
    const reactFlowNodes = visibleNodesRaw.map(node => {
      const level = treeStructure.levels[node.id] || 0;
      const hasChildren = (treeStructure.spanningTree[node.id] || []).length > 0;
      
      let nodeType = 'child';
      if (level === 0) nodeType = 'entry';
      else if (level === 1) nodeType = 'module';
      
      // External link check
      try {
        const hostname = new URL(node.url).hostname;
        if (startDomain && hostname !== startDomain) {
          nodeType = 'external';
        }
      } catch(e) {}

      // Calculate links count dynamically: children in tree + buttons in elements
      const linksCount = (node.elements?.buttons?.length || 0) + (treeStructure.spanningTree[node.id]?.length || 0);

      return {
        id: node.id,
        type: 'pageNode',
        data: {
          label: node.label || node.url,
          url: node.url,
          formsCount: node.elements?.forms?.length || 0,
          linksCount: linksCount || 2, // fallback to typical value if 0
          nodeType,
          isCollapsed: collapsedNodes.has(node.id),
          hasChildren,
          onToggleCollapse: () => handleCollapseToggle(node.id)
        }
      };
    });

    // Map edges: source to target must both be visible
    const visibleEdges = propEdges.filter(edge => 
      isNodeVisible(edge.source) && isNodeVisible(edge.target)
    ).map((edge, idx) => {
      // Check if target node has external type
      let isTargetExternal = false;
      try {
        const targetNode = propNodes.find(n => n.id === edge.target);
        if (targetNode) {
          const hostname = new URL(targetNode.url).hostname;
          isTargetExternal = startDomain && hostname !== startDomain;
        }
      } catch(e) {}

      return {
        id: `edge-${idx}`,
        source: edge.source,
        target: edge.target,
        type: 'smoothstep', // Orthogonal / elbow connectors
        animated: !isTargetExternal,
        style: { 
          stroke: isTargetExternal ? '#f97316' : 'rgba(255, 255, 255, 0.15)', 
          strokeWidth: 1.5,
          strokeDasharray: isTargetExternal ? '4,4' : undefined
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: isTargetExternal ? '#f97316' : 'rgba(255, 255, 255, 0.25)',
          width: 8,
          height: 8
        }
      };
    });

    const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
      reactFlowNodes, 
      visibleEdges
    );

    setNodes(layoutedNodes);
    setEdges(layoutedEdges);

    // Run fit view on load
    setTimeout(() => {
      fitView({ padding: 0.25 });
    }, 150);

  }, [propNodes, propEdges, collapsedNodes, treeStructure]);

  const handleCollapseToggle = (nodeId) => {
    setCollapsedNodes(prev => {
      const next = new Set(prev);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  };

  const handleExpandAll = () => {
    setCollapsedNodes(new Set());
  };

  const handleCollapseAll = () => {
    const allParents = new Set();
    Object.keys(treeStructure.spanningTree).forEach(nodeId => {
      const children = treeStructure.spanningTree[nodeId] || [];
      if (children.length > 0 && treeStructure.levels[nodeId] > 0) {
        allParents.add(nodeId);
      }
    });
    setCollapsedNodes(allParents);
  };

  const handleResetLayout = () => {
    setCollapsedNodes(new Set());
    setTimeout(() => {
      setViewport({ x: 0, y: 0, zoom: 1 }, { duration: 400 });
      fitView({ padding: 0.25 });
    }, 100);
  };

  // Fullscreen support event listener
  useEffect(() => {
    const handleFullscreenChange = () => {
      const isCurrentlyFullscreen = !!document.fullscreenElement;
      setIsFullscreen(isCurrentlyFullscreen);
      
      // Force React Flow viewport dimension recalculation
      window.dispatchEvent(new Event('resize'));
      
      setTimeout(() => {
        fitView({ padding: 0.25 });
      }, 300);
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
    };
  }, [fitView]);

  const handleFullscreenToggle = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen().catch((err) => {
        console.error("Error entering fullscreen:", err);
      });
    } else {
      document.exitFullscreen().catch((err) => {
        console.error("Error exiting fullscreen:", err);
      });
    }
  };

  // Export Map to vector SVG format
  const handleExportSvg = () => {
    if (!nodes.length) return;
    
    // Find min and max bounds for layout to size vector canvas
    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;
    nodes.forEach(n => {
      if (n.position.x < minX) minX = n.position.x;
      if (n.position.x > maxX) maxX = n.position.x;
      if (n.position.y < minY) minY = n.position.y;
      if (n.position.y > maxY) maxY = n.position.y;
    });

    const cardW = 240, cardH = 110;
    const padding = 100;
    const svgW = (maxX - minX) + cardW + padding * 2;
    const svgH = (maxY - minY) + cardH + padding * 2;

    const offsetX = -minX + padding;
    const offsetY = -minY + padding;

    let svgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${svgW} ${svgH}" width="${svgW}" height="${svgH}">`;
    svgStr += `<rect width="100%" height="100%" fill="#07070a"/>`;
    
    // Vector Styles matching UI variables
    svgStr += `<style>
      .edge { stroke: rgba(255, 255, 255, 0.25); stroke-width: 1.5; fill: none; }
      .node-box { fill: #111116; rx: 12; ry: 12; stroke-width: 1.5; }
      .border-entry { stroke: #4f46e5; }
      .border-module { stroke: #10b981; }
      .border-child { stroke: #20b8a6; }
      .border-external { stroke: #f97316; }
      .text-lbl { fill: #f4f4f9; font-family: system-ui, sans-serif; font-size: 13px; font-weight: 600; }
      .text-url { fill: #8e909e; font-family: monospace; font-size: 10px; }
      .badge-rect { rx: 10; ry: 10; }
      .badge-entry { fill: rgba(79, 70, 229, 0.2); }
      .badge-module { fill: rgba(16, 185, 129, 0.2); }
      .badge-child { fill: rgba(20, 184, 166, 0.2); }
      .badge-external { fill: rgba(249, 115, 22, 0.2); }
      .badge-text { font-family: system-ui, sans-serif; font-size: 9px; font-weight: 700; text-transform: uppercase; }
      .text-entry { fill: #818cf8; }
      .text-module { fill: #34d399; }
      .text-child { fill: #2dd4bf; }
      .text-external { fill: #fb923c; }
    </style>`;

    // Draw connection paths (orthogonal style mapping)
    edges.forEach(edge => {
      const src = nodes.find(n => n.id === edge.source);
      const tgt = nodes.find(n => n.id === edge.target);
      if (src && tgt) {
        const x1 = src.position.x + cardW / 2 + offsetX;
        const y1 = src.position.y + cardH + offsetY;
        const x2 = tgt.position.x + cardW / 2 + offsetX;
        const y2 = tgt.position.y + offsetY;
        const y_mid = (y1 + y2) / 2;
        // SVG elbow path definition
        svgStr += `<path d="M ${x1} ${y1} L ${x1} ${y_mid} L ${x2} ${y_mid} L ${x2} ${y2}" class="edge"/>`;
      }
    });

    // Draw card rectangles
    nodes.forEach(node => {
      const x = node.position.x + offsetX;
      const y = node.position.y + offsetY;
      const type = node.data.nodeType;
      const label = node.data.label;
      const url = node.data.url;
      const forms = node.data.formsCount;
      const links = node.data.linksCount;

      let borderClass = 'border-child';
      let badgeClass = 'badge-child';
      let badgeTextClass = 'text-child';
      let badgeTxt = 'Child Page';

      if (type === 'entry') {
        borderClass = 'border-entry';
        badgeClass = 'badge-entry';
        badgeTextClass = 'text-entry';
        badgeTxt = 'Entry Point';
      } else if (type === 'module') {
        borderClass = 'border-module';
        badgeClass = 'badge-module';
        badgeTextClass = 'text-module';
        badgeTxt = 'Module';
      } else if (type === 'external') {
        borderClass = 'border-external';
        badgeClass = 'badge-external';
        badgeTextClass = 'text-external';
        badgeTxt = 'External';
      }

      svgStr += `<g transform="translate(${x}, ${y})">`;
      // Card Container
      svgStr += `<rect width="${cardW}" height="${cardH}" class="node-box ${borderClass}"/>`;
      // Title
      svgStr += `<text x="15" y="30" class="text-lbl">${label.length > 22 ? label.slice(0, 20) + "..." : label}</text>`;
      // Path
      const pathStr = url.replace(/^https?:\/\/[^\/]+/, '') || '/';
      svgStr += `<text x="15" y="55" class="text-url">${pathStr.length > 30 ? pathStr.slice(0, 27) + "..." : pathStr}</text>`;
      // Stats/Badge Footer
      svgStr += `<rect x="15" y="75" width="80" height="18" class="badge-rect ${badgeClass}"/>`;
      svgStr += `<text x="23" y="87" class="badge-text ${badgeTextClass}">${badgeTxt}</text>`;
      svgStr += `</g>`;
    });

    svgStr += `</svg>`;

    const blob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' });
    const blobUrl = URL.createObjectURL(blob);
    const downloadLink = document.createElement('a');
    downloadLink.href = blobUrl;
    downloadLink.download = `${label_filename(propNodes[0]?.label || 'website')}-architecture.svg`;
    document.body.appendChild(downloadLink);
    downloadLink.click();
    document.body.removeChild(downloadLink);
  };

  const label_filename = (val) => {
    return val.toLowerCase().replace(/[^a-z0-9]/g, '_');
  };

  // Click on a node sends selection details to App.jsx sidebar
  const handleNodeClick = (event, node) => {
    const rawNode = propNodes.find(n => n.id === node.id);
    if (rawNode && onNodeSelect) {
      // Resolve children and parents dynamically to pass to sidebar
      const parentId = treeStructure.parentMap[node.id];
      const parentNode = propNodes.find(n => n.id === parentId);
      
      const childrenIds = treeStructure.spanningTree[node.id] || [];
      const childrenNodes = childrenIds.map(cid => {
        const childNode = propNodes.find(n => n.id === cid);
        return childNode ? {
          id: childNode.id,
          label: childNode.label,
          url: childNode.url,
          emoji: getIconEmoji(childNode.label, childNode.url, treeStructure.levels[childNode.id] === 1 ? 'module' : 'child')
        } : null;
      }).filter(Boolean);

      onNodeSelect({
        ...rawNode,
        emoji: getIconEmoji(rawNode.label, rawNode.url, treeStructure.levels[rawNode.id] === 0 ? 'entry' : treeStructure.levels[rawNode.id] === 1 ? 'module' : 'child'),
        nodeType: treeStructure.levels[rawNode.id] === 0 ? 'entry' : treeStructure.levels[rawNode.id] === 1 ? 'module' : 'child',
        parentName: parentNode ? parentNode.label : 'None',
        parentEmoji: parentNode ? getIconEmoji(parentNode.label, parentNode.url, treeStructure.levels[parentNode.id] === 0 ? 'entry' : 'module') : '',
        connectedChildren: childrenNodes
      });
    }
  };

  // Hover Tooltip logic
  const handleNodeMouseEnter = (event, node) => {
    const rawNode = propNodes.find(n => n.id === node.id);
    if (!rawNode || !containerRef.current) return;
    
    const containerRect = containerRef.current.getBoundingClientRect();
    
    // Find parent name
    const parentId = treeStructure.parentMap[node.id];
    const parentNode = propNodes.find(n => n.id === parentId);
    const parentLabel = parentNode ? parentNode.label : 'None';

    // Find children names
    const childrenIds = treeStructure.spanningTree[node.id] || [];
    const childrenLabels = childrenIds.map(cid => {
      const childNode = propNodes.find(n => n.id === cid);
      return childNode ? childNode.label : cid;
    });

    setTooltipPos({
      x: event.clientX - containerRect.left + 15,
      y: event.clientY - containerRect.top + 15
    });

    setHoveredNode({
      ...rawNode,
      parentLabel,
      childrenLabels
    });
  };

  const handleNodeMouseLeave = () => {
    setHoveredNode(null);
  };

  return (
    <div 
      className="map-canvas-container" 
      ref={containerRef}
      style={{ 
        height: isFullscreen ? '100vh' : '530px', 
        width: '100%', 
        position: 'relative',
        display: 'flex',
        flexDirection: 'column'
      }}
    >
      
      {/* 1. TOP TOOLBAR CONTROLS */}
      <div className="map-toolbar">
        <div className="toolbar-section">
          <h4 style={{ fontFamily: 'var(--font-title)', fontWeight: 600, fontSize: '0.95rem' }}>Discovered Pages Architecture</h4>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>Visual representation of all discovered pages and their relationships</span>
        </div>
        
        <div className="toolbar-actions">
          <button className="toolbar-btn" onClick={() => fitView({ padding: 0.25 })} title="Fit View">
            <Maximize2 size={13} />
            <span>Fit View</span>
          </button>
          <button className="toolbar-btn" onClick={() => zoomIn()} title="Zoom In">
            <ZoomIn size={13} />
            <span>Zoom In</span>
          </button>
          <button className="toolbar-btn" onClick={() => zoomOut()} title="Zoom Out">
            <ZoomOut size={13} />
            <span>Zoom Out</span>
          </button>
          <button className="toolbar-btn" onClick={handleExpandAll} title="Expand All">
            <Folders size={13} />
            <span>Expand All</span>
          </button>
          <button className="toolbar-btn" onClick={handleCollapseAll} title="Collapse All">
            <ChevronRight size={13} />
            <span>Collapse All</span>
          </button>
          <button className="toolbar-btn" onClick={handleResetLayout} title="Reset Layout">
            <RefreshCw size={13} />
            <span>Reset Layout</span>
          </button>
          <button className="toolbar-btn primary-action" onClick={handleExportSvg} title="Export SVG Map">
            <Download size={13} />
            <span>Export Map</span>
          </button>
          <button className="toolbar-btn" onClick={handleFullscreenToggle} style={{ padding: '0.45rem' }}>
            {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>

      {/* 2. REACT FLOW VISUALIZATION CANVAS */}
      <div style={{ height: isFullscreen ? 'calc(100vh - 80px)' : '470px', position: 'relative', width: '100%', background: '#0b0b10' }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          onNodeClick={handleNodeClick}
          onNodeMouseEnter={handleNodeMouseEnter}
          onNodeMouseLeave={handleNodeMouseLeave}
          fitView
          fitViewOptions={{ padding: 0.25 }}
          minZoom={0.15}
          maxZoom={2.0}
        >
          <Background color="rgba(255, 255, 255, 0.05)" gap={16} size={1} />
        </ReactFlow>

        {/* Hover Tooltip Overlay */}
        {hoveredNode && (
          <div 
            className="node-hover-tooltip"
            style={{
              left: `${tooltipPos.x}px`,
              top: `${tooltipPos.y}px`
            }}
          >
            <div style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: '4px', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '3px' }}>
              {hoveredNode.title || hoveredNode.label}
            </div>
            <div className="tooltip-row"><strong>URL:</strong> <span className="tooltip-url">{hoveredNode.url}</span></div>
            <div className="tooltip-row"><strong>Forms:</strong> {hoveredNode.elements?.forms?.length || 0}</div>
            <div className="tooltip-row"><strong>Buttons:</strong> {hoveredNode.elements?.buttons?.length || 0}</div>
            <div className="tooltip-row"><strong>Selects:</strong> {hoveredNode.elements?.selects?.length || 0}</div>
            <div className="tooltip-row"><strong>Parent Page:</strong> {hoveredNode.parentLabel}</div>
            {hoveredNode.childrenLabels?.length > 0 && (
              <div className="tooltip-row" style={{ marginTop: '2px' }}>
                <strong>Child Pages:</strong>
                <div style={{ paddingLeft: '8px', color: 'var(--text-muted)' }}>
                  {hoveredNode.childrenLabels.slice(0, 4).map((c, i) => (
                    <div key={i}>• {c}</div>
                  ))}
                  {hoveredNode.childrenLabels.length > 4 && <div>• +{hoveredNode.childrenLabels.length - 4} more</div>}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 3. LEGEND PANEL BAR */}
      <div className="map-legend">
        <span className="legend-title">Legend:</span>
        <div className="legend-item">
          <span className="legend-dot dot-entry"></span>
          <span>Entry Point</span>
        </div>
        <div className="legend-item">
          <span className="legend-dot dot-module"></span>
          <span>Module</span>
        </div>
        <div className="legend-item">
          <span className="legend-dot dot-child"></span>
          <span>Child Page</span>
        </div>
        <div className="legend-item">
          <span className="legend-dot dot-external"></span>
          <span>External Link</span>
        </div>
      </div>

    </div>
  );
}

export default function CrawlerMap(props) {
  return (
    <ReactFlowProvider>
      <CrawlerMapContent {...props} />
    </ReactFlowProvider>
  );
}
