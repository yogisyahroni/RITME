const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let ritmeProcess = null;
let ritmePort = 8787;

function getBackendDir() {
    const dir = path.join(__dirname, '..', 'ritme_backend');
    if (!fs.existsSync(dir)) {
        throw new Error(`RITME backend not found at ${dir}`);
    }
    return dir;
}

function findPython() {
    // Try common Python paths
    const candidates = [
        path.join(getBackendDir(), '..', 'venv_311', 'Scripts', 'python.exe'),
        path.join(getBackendDir(), '..', '.venv', 'Scripts', 'python.exe'),
        'python',
        'python3',
        process.platform === 'win32' ? 'py' : null
    ].filter(Boolean);
    
    for (const cmd of candidates) {
        try {
            const result = require('child_process').spawnSync(cmd, ['--version'], { timeout: 3000 });
            if (result.status === 0) return cmd;
        } catch {}
    }
    return null;
}

function startRitmeBackend() {
    return new Promise((resolve, reject) => {
        if (ritmeProcess) {
            console.log('[RITME] Backend already running');
            resolve(ritmePort);
            return;
        }

        const backendDir = getBackendDir();
        const pythonCmd = findPython();
        
        if (!pythonCmd) {
            reject(new Error('Python not found. Install Python 3.10+ to use RITME pipeline.'));
            return;
        }

        const env = {
            ...process.env,
            PORT: String(ritmePort),
            PYTHONUNBUFFERED: '1',
            RITME_BACKEND_DIR: backendDir,
        };

        console.log(`[RITME] Starting backend on port ${ritmePort}`);
        console.log(`[RITME] Python: ${pythonCmd}`);
        console.log(`[RITME] Dir: ${backendDir}`);

        // First check if requirements are installed
        const checkResult = require('child_process').spawnSync(pythonCmd, [
            '-c', 'import fastapi, uvicorn, dotenv, requests'
        ], {
            cwd: backendDir,
            timeout: 10000,
        });

        if (checkResult.status !== 0) {
            console.log('[RITME] Installing dependencies...');
            const installResult = require('child_process').spawnSync(pythonCmd, [
                '-m', 'pip', 'install', '-r', 'requirements.txt'
            ], {
                cwd: backendDir,
                timeout: 120000,
                stdio: 'inherit',
            });
            if (installResult.status !== 0) {
                reject(new Error('Failed to install Python dependencies'));
                return;
            }
        }

        // Copy .env.example to .env if .env doesn't exist
        const envExample = path.join(backendDir, '.env.example');
        const envFile = path.join(backendDir, '.env');
        if (fs.existsSync(envExample) && !fs.existsSync(envFile)) {
            fs.copyFileSync(envExample, envFile);
            console.log('[RITME] Created .env from .env.example');
        }

        ritmeProcess = spawn(pythonCmd, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(ritmePort), '--log-level', 'warning'], {
            cwd: backendDir,
            env,
            stdio: ['pipe', 'pipe', 'pipe'],
            windowsHide: true,
        });

        let started = false;
        const timeout = setTimeout(() => {
            if (!started) {
                console.log('[RITME] Backend startup timed out');
                reject(new Error('Backend startup timeout'));
            }
        }, 30000);

        ritmeProcess.stdout.on('data', (data) => {
            const text = data.toString();
            console.log(`[RITME] ${text.trim()}`);
            if (text.includes('Uvicorn running on') || text.includes('Application startup complete')) {
                started = true;
                clearTimeout(timeout);
                console.log(`[RITME] Backend ready on port ${ritmePort}`);
                resolve(ritmePort);
            }
        });

        ritmeProcess.stderr.on('data', (data) => {
            const text = data.toString();
            if (text.includes('Uvicorn running') || text.includes('Application startup')) {
                started = true;
                clearTimeout(timeout);
                console.log(`[RITME] Backend ready on port ${ritmePort}`);
                resolve(ritmePort);
                return;
            }
            console.log(`[RITME] ${text.trim()}`);
        });

        ritmeProcess.on('error', (err) => {
            console.error(`[RITME] Process error: ${err.message}`);
            reject(err);
        });

        ritmeProcess.on('exit', (code) => {
            console.log(`[RITME] Process exited with code ${code}`);
            ritmeProcess = null;
        });
    });
}

function stopRitmeBackend() {
    if (ritmeProcess) {
        console.log('[RITME] Stopping backend...');
        if (process.platform === 'win32') {
            spawn('taskkill', ['/pid', String(ritmeProcess.pid), '/f', '/t']);
        } else {
            ritmeProcess.kill('SIGTERM');
        }
        ritmeProcess = null;
    }
}

function getRitmeStatus() {
    return {
        running: ritmeProcess !== null,
        port: ritmePort,
        pid: ritmeProcess ? ritmeProcess.pid : null,
    };
}

module.exports = { startRitmeBackend, stopRitmeBackend, getRitmeStatus };
