/**
 * BlackRoad OS - Galaxy View Component
 *
 * Visualizes 30,000 agents as a 3D galaxy:
 * - Organizations = Solar systems
 * - Repositories = Planets
 * - Agents = Particles orbiting planets
 */

import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls';

interface Agent {
  id: string;
  name: string;
  type: string;
  core: string;
  status: 'active' | 'idle' | 'error' | 'offline';
  position?: THREE.Vector3;
  velocity?: THREE.Vector3;
}

interface GalaxyViewProps {
  agents: Agent[];
  onAgentClick?: (agent: Agent) => void;
}

export const GalaxyView: React.FC<GalaxyViewProps> = ({ agents, onAgentClick }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<THREE.Scene>();
  const cameraRef = useRef<THREE.PerspectiveCamera>();
  const rendererRef = useRef<THREE.WebGLRenderer>();
  const controlsRef = useRef<OrbitControls>();
  const particlesRef = useRef<THREE.Points>();
  const [hoveredAgent, setHoveredAgent] = useState<Agent | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Initialize scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x000000);
    scene.fog = new THREE.FogExp2(0x000000, 0.0008);
    sceneRef.current = scene;

    // Initialize camera
    const camera = new THREE.PerspectiveCamera(
      75,
      window.innerWidth / window.innerHeight,
      0.1,
      2000
    );
    camera.position.z = 500;
    cameraRef.current = camera;

    // Initialize renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    containerRef.current.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Initialize controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.minDistance = 50;
    controls.maxDistance = 1500;
    controlsRef.current = controls;

    // Add ambient light
    const ambientLight = new THREE.AmbientLight(0x404040, 2);
    scene.add(ambientLight);

    // Add central "sun" (BlackRoad core)
    const sunGeometry = new THREE.SphereGeometry(20, 32, 32);
    const sunMaterial = new THREE.MeshBasicMaterial({
      color: 0xFF1D6C, // Hot Pink
      emissive: 0xFF1D6C,
      emissiveIntensity: 1
    });
    const sun = new THREE.Mesh(sunGeometry, sunMaterial);
    scene.add(sun);

    // Add sun glow
    const glowGeometry = new THREE.SphereGeometry(25, 32, 32);
    const glowMaterial = new THREE.ShaderMaterial({
      uniforms: {
        glowColor: { value: new THREE.Color(0xFF1D6C) }
      },
      vertexShader: `
        varying vec3 vNormal;
        void main() {
          vNormal = normalize(normalMatrix * normal);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform vec3 glowColor;
        varying vec3 vNormal;
        void main() {
          float intensity = pow(0.7 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.0);
          gl_FragColor = vec4(glowColor, 1.0) * intensity;
        }
      `,
      side: THREE.BackSide,
      blending: THREE.AdditiveBlending,
      transparent: true
    });
    const glow = new THREE.Mesh(glowGeometry, glowMaterial);
    scene.add(glow);

    // Create particle system for agents
    createParticleSystem(scene, agents);

    // Add stars background
    addStars(scene);

    // Handle window resize
    const handleResize = () => {
      if (!camera || !renderer) return;
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    };
    window.addEventListener('resize', handleResize);

    // Animation loop
    const animate = () => {
      requestAnimationFrame(animate);

      // Rotate sun slowly
      sun.rotation.y += 0.001;
      glow.rotation.y += 0.001;

      // Update particle positions (orbital motion)
      if (particlesRef.current) {
        const positions = particlesRef.current.geometry.attributes.position.array as Float32Array;
        const colors = particlesRef.current.geometry.attributes.color.array as Float32Array;

        for (let i = 0; i < agents.length; i++) {
          const agent = agents[i];
          const idx = i * 3;

          // Simple orbital motion around origin
          const radius = Math.sqrt(positions[idx] ** 2 + positions[idx + 2] ** 2);
          const angle = Math.atan2(positions[idx + 2], positions[idx]);
          const newAngle = angle + (0.001 / (radius / 100)); // Slower orbit for distant agents

          positions[idx] = radius * Math.cos(newAngle);
          positions[idx + 2] = radius * Math.sin(newAngle);

          // Update colors based on status
          const color = getStatusColor(agent.status);
          colors[idx] = color.r;
          colors[idx + 1] = color.g;
          colors[idx + 2] = color.b;
        }

        particlesRef.current.geometry.attributes.position.needsUpdate = true;
        particlesRef.current.geometry.attributes.color.needsUpdate = true;
      }

      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    // Cleanup
    return () => {
      window.removeEventListener('resize', handleResize);
      renderer.dispose();
      containerRef.current?.removeChild(renderer.domElement);
    };
  }, []);

  // Update particles when agents change
  useEffect(() => {
    if (sceneRef.current && agents.length > 0) {
      createParticleSystem(sceneRef.current, agents);
    }
  }, [agents]);

  const createParticleSystem = (scene: THREE.Scene, agents: Agent[]) => {
    // Remove old particles
    if (particlesRef.current) {
      scene.remove(particlesRef.current);
      particlesRef.current.geometry.dispose();
      (particlesRef.current.material as THREE.Material).dispose();
    }

    const particleCount = agents.length;
    const geometry = new THREE.BufferGeometry();

    const positions = new Float32Array(particleCount * 3);
    const colors = new Float32Array(particleCount * 3);
    const sizes = new Float32Array(particleCount);

    // Organize agents into clusters by core
    const cores = ['alice', 'aria', 'octavia', 'lucidia', 'cloud'];
    const agentsByCore = new Map<string, Agent[]>();
    cores.forEach(core => agentsByCore.set(core, []));
    agents.forEach(agent => {
      const coreAgents = agentsByCore.get(agent.core) || [];
      coreAgents.push(agent);
      agentsByCore.set(agent.core, coreAgents);
    });

    let idx = 0;
    agentsByCore.forEach((coreAgents, core) => {
      const coreIdx = cores.indexOf(core);
      const coreAngle = (coreIdx / cores.length) * Math.PI * 2;
      const coreRadius = 200 + coreIdx * 50; // Stagger cores radially

      coreAgents.forEach((agent, agentIdx) => {
        const i = idx * 3;

        // Position agents in a cluster around their core
        const agentAngle = coreAngle + (agentIdx / coreAgents.length) * 0.3; // Small angular spread
        const agentRadius = coreRadius + (Math.random() - 0.5) * 100; // Radial spread
        const y = (Math.random() - 0.5) * 50; // Vertical spread

        positions[i] = agentRadius * Math.cos(agentAngle);
        positions[i + 1] = y;
        positions[i + 2] = agentRadius * Math.sin(agentAngle);

        // Color based on status
        const color = getStatusColor(agent.status);
        colors[i] = color.r;
        colors[i + 1] = color.g;
        colors[i + 2] = color.b;

        // Size based on activity
        sizes[idx] = agent.status === 'active' ? 3 : 1.5;

        idx++;
      });
    });

    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    const material = new THREE.PointsMaterial({
      size: 3,
      vertexColors: true,
      transparent: true,
      opacity: 0.8,
      sizeAttenuation: true
    });

    const particles = new THREE.Points(geometry, material);
    scene.add(particles);
    particlesRef.current = particles;
  };

  const addStars = (scene: THREE.Scene) => {
    const starGeometry = new THREE.BufferGeometry();
    const starCount = 5000;
    const positions = new Float32Array(starCount * 3);

    for (let i = 0; i < starCount * 3; i++) {
      positions[i] = (Math.random() - 0.5) * 2000;
    }

    starGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    const starMaterial = new THREE.PointsMaterial({
      color: 0xFFFFFF,
      size: 1,
      transparent: true,
      opacity: 0.3
    });

    const stars = new THREE.Points(starGeometry, starMaterial);
    scene.add(stars);
  };

  const getStatusColor = (status: string): THREE.Color => {
    switch (status) {
      case 'active': return new THREE.Color(0x00FF00); // Green
      case 'idle': return new THREE.Color(0xF5A623);   // Amber
      case 'error': return new THREE.Color(0xFF1D6C);   // Hot Pink
      case 'offline': return new THREE.Color(0x666666); // Gray
      default: return new THREE.Color(0xFFFFFF);
    }
  };

  return (
    <div style={{ position: 'relative', width: '100%', height: '100vh' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

      {/* Stats overlay */}
      <div style={{
        position: 'absolute',
        top: '20px',
        left: '20px',
        background: 'rgba(0, 0, 0, 0.7)',
        padding: '20px',
        borderRadius: '10px',
        color: '#FFFFFF',
        fontFamily: 'SF Pro Display, sans-serif',
        backdropFilter: 'blur(10px)'
      }}>
        <h2 style={{ margin: '0 0 15px 0', color: '#FF1D6C' }}>
          🖤🛣️ BlackRoad Galaxy
        </h2>
        <div style={{ fontSize: '14px', lineHeight: '1.6' }}>
          <div>Total Agents: <strong>{agents.length.toLocaleString()}</strong></div>
          <div>Active: <strong style={{ color: '#00FF00' }}>
            {agents.filter(a => a.status === 'active').length.toLocaleString()}
          </strong></div>
          <div>Idle: <strong style={{ color: '#F5A623' }}>
            {agents.filter(a => a.status === 'idle').length.toLocaleString()}
          </strong></div>
          <div>Error: <strong style={{ color: '#FF1D6C' }}>
            {agents.filter(a => a.status === 'error').length.toLocaleString()}
          </strong></div>
          <div>Offline: <strong style={{ color: '#666666' }}>
            {agents.filter(a => a.status === 'offline').length.toLocaleString()}
          </strong></div>
        </div>
      </div>

      {/* Controls info */}
      <div style={{
        position: 'absolute',
        bottom: '20px',
        right: '20px',
        background: 'rgba(0, 0, 0, 0.7)',
        padding: '15px',
        borderRadius: '10px',
        color: '#FFFFFF',
        fontFamily: 'SF Mono, monospace',
        fontSize: '12px',
        backdropFilter: 'blur(10px)'
      }}>
        <div>🖱️ Drag: Rotate view</div>
        <div>🔍 Scroll: Zoom in/out</div>
        <div>🎯 Click: Select agent</div>
      </div>
    </div>
  );
};

export default GalaxyView;
