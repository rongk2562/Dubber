/**
 * MongoDB & Mongoose Version & Auto-Update Server
 * Manages version releases, changelogs, and .exe downloads for AI Movie Dubbing Studio
 */

const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const os = require('os');

const app = express();
const PORT = process.env.PORT || 5000;
const MONGO_URI = process.env.MONGO_URI || 'mongodb://localhost:27017/aidub_updates';

// Get LAN IPv4 addresses (Wi-Fi / Ethernet)
function getLanIps() {
  const interfaces = os.networkInterfaces();
  const ips = [];
  for (const name of Object.keys(interfaces)) {
    for (const net of interfaces[name]) {
      if (net.family === 'IPv4' && !net.internal && !net.address.startsWith('127.')) {
        ips.push({ name, ip: net.address });
      }
    }
  }
  return ips;
}

// Ensure downloads directory exists
const DOWNLOADS_DIR = path.join(__dirname, 'public', 'downloads');
if (!fs.existsSync(DOWNLOADS_DIR)) {
  fs.mkdirSync(DOWNLOADS_DIR, { recursive: true });
}

// Multer storage for .exe uploads
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, DOWNLOADS_DIR),
  filename: (req, file, cb) => {
    // Keep original filename or sanitize
    const name = file.originalname || 'AI_Movie_Dubbing_Studio.exe';
    cb(null, name);
  }
});
const upload = multer({ storage });

// Middlewares
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname, 'public')));

// --- Mongoose Schema & Model ---
const VersionSchema = new mongoose.Schema({
  version: {
    type: String,
    required: true,
    unique: true,
    trim: true
  },
  download_url: {
    type: String,
    required: true,
    trim: true
  },
  release_date: {
    type: String,
    default: () => new Date().toISOString().slice(0, 10)
  },
  changelog_km: {
    type: String,
    default: ''
  },
  changelog_en: {
    type: String,
    default: ''
  },
  mandatory: {
    type: Boolean,
    default: false
  },
  created_at: {
    type: Date,
    default: Date.now
  }
});

const Version = mongoose.model('Version', VersionSchema);

// --- Connect to MongoDB ---
mongoose.connect(MONGO_URI)
  .then(async () => {
    console.log(`[+] Connected to MongoDB successfully at: ${MONGO_URI}`);
    await autoSeedInitialVersion();
  })
  .catch(err => {
    console.error(`[!] MongoDB Connection Error:`, err.message);
  });

async function autoSeedInitialVersion() {
  try {
    const versionJsonPath = path.join(__dirname, '..', 'version.json');
    if (fs.existsSync(versionJsonPath)) {
      const vData = JSON.parse(fs.readFileSync(versionJsonPath, 'utf-8'));
      if (vData && vData.version) {
        await Version.findOneAndUpdate(
          { version: vData.version },
          {
            version: vData.version,
            download_url: vData.download_url || `http://localhost:${PORT}/downloads/AI_Movie_Dubbing_Studio.exe`,
            release_date: vData.release_date || new Date().toISOString().slice(0, 10),
            changelog_km: vData.changelog_km || '',
            changelog_en: vData.changelog_en || '',
            mandatory: Boolean(vData.mandatory),
            created_at: new Date()
          },
          { upsert: true, new: true }
        );
        console.log(`[*] Synced latest release v${vData.version} from version.json into MongoDB.`);
        return;
      }
    }
    const count = await Version.countDocuments();
    if (count === 0) {
      await Version.create({
        version: '1.1.0',
        download_url: `http://localhost:${PORT}/downloads/AI_Movie_Dubbing_Studio.exe`,
        release_date: new Date().toISOString().slice(0, 10),
        changelog_km: 'កំណែថ្មី v1.1.0:\n- បកប្រែ និងបញ្ចូលសំឡេង AI ច្រើនភាសា (៩ ភាសា)\n- ប៊ូតុង សម្អាត (Clear) ៣ ជម្រើស',
        changelog_en: 'Release v1.1.0 with 9-language translation, neural TTS voices, and Clear tool',
        mandatory: false
      });
      console.log(`[*] Auto-seeded v1.1.0 release into MongoDB.`);
    }
  } catch (e) {
    console.error(`[!] Seed note:`, e.message);
  }
}

// --- API Endpoints ---

/**
 * GET /api/version
 * Primary endpoint queried by Python app/updater.py
 * Returns the latest release matching UpdateInfo format (with fallback to version.json)
 */
app.get('/api/version', async (req, res) => {
  try {
    if (mongoose.connection.readyState === 1) {
      const latest = await Version.findOne().sort({ created_at: -1, _id: -1 });
      if (latest) {
        return res.json({
          version: latest.version,
          download_url: latest.download_url,
          release_date: latest.release_date,
          changelog_km: latest.changelog_km,
          changelog_en: latest.changelog_en,
          mandatory: latest.mandatory
        });
      }
    }
    // Fallback to version.json if MongoDB is disconnected or empty
    const versionJsonPath = path.join(__dirname, '..', 'version.json');
    if (fs.existsSync(versionJsonPath)) {
      const vData = JSON.parse(fs.readFileSync(versionJsonPath, 'utf-8'));
      return res.json(vData);
    }
    return res.status(404).json({ error: 'No version releases found' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/**
 * GET /api/version/all
 * Lists all released versions in chronological order
 */
app.get('/api/version/all', async (req, res) => {
  try {
    const all = await Version.find().sort({ created_at: -1 });
    res.json(all);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/**
 * POST /api/version
 * Publish a new version to MongoDB
 */
app.post('/api/version', async (req, res) => {
  try {
    const { version, download_url, release_date, changelog_km, changelog_en, mandatory } = req.body;
    if (!version || !download_url) {
      return res.status(400).json({ error: 'Fields "version" and "download_url" are required.' });
    }

    const doc = await Version.findOneAndUpdate(
      { version },
      {
        version,
        download_url,
        release_date: release_date || new Date().toISOString().slice(0, 10),
        changelog_km: changelog_km || '',
        changelog_en: changelog_en || '',
        mandatory: Boolean(mandatory),
        created_at: new Date()
      },
      { upsert: true, new: true }
    );

    console.log(`[+] Successfully published version v${doc.version} to MongoDB!`);
    res.json({ success: true, message: `Version v${doc.version} published successfully!`, data: doc });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/**
 * POST /api/upload-exe
 * Uploads a compiled .exe file and stores in public/downloads
 */
app.post('/api/upload-exe', upload.single('file'), (req, res) => {
  if (!req.file) {
    return res.status(400).json({ error: 'No file uploaded.' });
  }
  const host = req.get('host');
  const protocol = req.protocol;
  const fileUrl = `${protocol}://${host}/downloads/${encodeURIComponent(req.file.filename)}`;
  res.json({
    success: true,
    filename: req.file.filename,
    size: req.file.size,
    download_url: fileUrl
  });
});

/**
 * GET /api/network-info
 * Returns local LAN IPs and update URLs for configuring client PCs
 */
app.get('/api/network-info', (req, res) => {
  const lanIps = getLanIps();
  const host = req.get('host') || `localhost:${PORT}`;
  res.json({
    port: PORT,
    localhost_url: `http://localhost:${PORT}/api/version`,
    lan_ips: lanIps,
    lan_urls: lanIps.map(item => ({
      name: item.name,
      ip: item.ip,
      update_url: `http://${item.ip}:${PORT}/api/version`
    })),
    current_request_url: `${req.protocol}://${host}/api/version`
  });
});

// Start Server
app.listen(PORT, () => {
  const lanIps = getLanIps();
  const primaryLan = lanIps.find(n => n.name.toLowerCase().includes('wi-fi') || n.name.toLowerCase().includes('wireless')) || lanIps[0];
  const lanUrl = primaryLan ? `http://${primaryLan.ip}:${PORT}/api/version` : `http://YOUR_LAN_IP:${PORT}/api/version`;

  console.log(`================================================================`);
  console.log(`🚀 Mongoose Update Server is running on port ${PORT}!`);
  console.log(`   - Local Endpoint:    http://localhost:${PORT}/api/version`);
  console.log(`   - Wi-Fi/LAN Endpoint: ${lanUrl}`);
  console.log(`     (ប្រើ Link នេះសម្រាប់ PC ផ្សេងលើ Wi-Fi ជាមួយគ្នា)`);
  console.log(`   - Admin Dashboard:   http://localhost:${PORT}/`);
  console.log(`   - MongoDB URI:       ${MONGO_URI}`);
  console.log(`================================================================`);
});
