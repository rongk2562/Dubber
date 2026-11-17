# 🌐 មគ្គុទ្ទេសក៍ដាក់ Server Update លើ Cloud ២៤/៧ (Render.com + MongoDB Atlas)
**AI Movie Dubbing Studio - Worldwide Auto-Update System**

មគ្គុទ្ទេសក៍នេះនឹងណែនាំអ្នកមួយជំហានម្តងៗ ក្នុងការដាក់ Mongoose Update Server លើ Cloud ដោយឥតគិតថ្លៃ ១០០% (Free Tier) ដើម្បីឱ្យកម្មវិធី `.exe` លើកុំព្យូទ័រ PC ផ្សេងៗទូទាំងពិភពលោក អាចពិនិត្យមើល និងទាញយក Update បានគ្រប់ពេលវេលា ទោះបីជាអ្នកបិទកុំព្យូទ័ររបស់អ្នកក៏ដោយ។

---

## ជំហានទី ១៖ បង្កើត MongoDB Atlas (Cloud Database - ឥតគិតថ្លៃ)

1. ចូលទៅកាន់គេហទំព័រ [https://www.mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas)
2. ចុច **Sign In** (អ្នកអាចជ្រើសរើស **Sign in with Google** ដើម្បីងាយស្រួល)
3. បង្កើត Cluster ដោយជ្រើសរើស **M0 Free (Shared Sandbox - ឥតគិតថ្លៃរហូត)**
4. បង្កើត **Database User & Password**៖
   - ឧទាហរណ៍៖ Username: `aidubadmin` | Password: `your_secure_password_123`
   - *សូមកត់ត្រា Password នេះទុក*
5. កំណត់ **Network Access** (ដើម្បីឱ្យ Render អាចភ្ជាប់បាន)៖
   - ចូលទៅម៉ឺនុយ **Network Access** នៅខាងឆ្វេង
   - ចុច **Add IP Address**
   - ជ្រើសរើស **Allow Access from Anywhere (`0.0.0.0/0`)** រួចចុច Confirm
6. យក **Connection String**៖
   - ចូលទៅ **Database** -> ចុចប៊ូតុង **Connect** លើ Cluster របស់អ្នក
   - ជ្រើសរើស **Drivers** (Node.js)
   - អ្នកនឹងឃើញ Connection String ទម្រង់ដូចខាងក្រោម៖
     ```text
     mongodb+srv://aidubadmin:<password>@cluster0.abcde.mongodb.net/aidub_updates?retryWrites=true&w=majority
     ```
   - ជំនួស `<password>` ដោយ Password ពិតរបស់អ្នក (ឧ. `your_secure_password_123`)

---

## ជំហានទី ២៖ ដាក់ Server លើ Render.com (Free Web Service - ដំណើរការ ២៤/៧)

1. ចូលទៅកាន់គេហទំព័រ [https://render.com](https://render.com) (ចុះឈ្មោះ ឬ Sign in with Google / GitHub)
2. បង្កើត GitHub Repository មួយ (ឧ. ឈ្មោះ `aidub-update-server`) ដោយ Upload ឯកសារក្នុងថត `server/`៖
   - `server.js`
   - `package.json`
   - `package-lock.json`
   - ថត `public/` (ដែលមាន `index.html`)
3. ត្រឡប់មក Render.com ចុច **New +** -> ជ្រើសរើស **Web Service**
4. ជ្រើសរើស GitHub Repository ដែលអ្នកទើបបង្កើត
5. បំពេញព័ត៌មានកំណត់៖
   - **Name:** `aidub-update-server` (ឬឈ្មោះតាមចិត្ត)
   - **Region:** Singapore (ជិតកម្ពុជា លឿនបំផុត)
   - **Branch:** `main`
   - **Runtime:** `Node`
   - **Build Command:** `npm install`
   - **Start Command:** `node server.js`
   - **Instance Type:** `Free`
6. បញ្ចូល **Environment Variables** (នៅផ្នែកខាងក្រោម)៖
   - **Key:** `MONGO_URI`
   - **Value:** `mongodb+srv://aidubadmin:your_secure_password_123@cluster0.abcde.mongodb.net/aidub_updates?retryWrites=true&w=majority`
7. ចុចប៊ូតុង **Create Web Service**
8. រង់ចាំប្រហែល ១-២ នាទី អ្នកនឹងឃើញពាក្យ `Live` ពណ៌បៃតង និងទទួលបាន Domain ដូចជា៖
   ```text
   https://aidub-update-server.onrender.com
   ```

---

## ជំហានទី ៣៖ គ្រប់គ្រងកំណែ Version និង Link ទាញយក .EXE តាម Web Dashboard

1. បើក Browser លើ PC ឬទូរស័ព្ទរបស់អ្នក ចូលទៅកាន់ URL របស់ Render៖
   ```text
   https://aidub-update-server.onrender.com/
   ```
2. អ្នកនឹងឃើញផ្ទាំង **Mongoose Version Control Dashboard** ដែលរត់លើ Cloud ផ្ទាល់!
3. **កន្លែងដាក់ Download URL នៃឯកសារ .EXE ថ្មី៖**
   ដោយសារ Render Free tier មានទំហំ Disk កំណត់ ជម្រើសល្អបំផុតសម្រាប់ដាក់ឯកសារ `.exe` (ទំហំធំ) គឺ៖
   - **ជម្រើស A:** Upload ឯកសារ `AI_Movie_Dubbing_Studio.exe` ទៅកាន់ **Google Drive** រួចយក Direct Download Link
   - **ជម្រើស B:** Upload ទៅកាន់ **GitHub Releases** (លឿន និងឥតគិតថ្លៃទំហំរហូតដល់ 2GB)
   - **ជម្រើស C:** Upload ទៅកាន់ **MediaFire** ឬ **Dropbox**
4. បញ្ចូលលេខ Version (ឧ. `1.0.1`), កាលបរិច្ឆេទ, Changelog ជាភាសាខ្មែរ រួចចុច **🚀 ចេញផ្សាយកំណែនេះទៅកាន់ MongoDB**។

---

## ជំហានទី ៤៖ កំណត់ URL ក្នុងកម្មវិធី និងបង្កើតឯកសារ .EXE

1. បើកឯកសារ `app/config.py` រួចកែសម្រួលបន្ទាត់ `DEFAULT_UPDATE_URL`៖
   ```python
   DEFAULT_UPDATE_URL = "https://aidub-update-server.onrender.com/api/version"
   ```
2. បើក Terminal ឬ PowerShell រួចបង្កើតឯកសារ `.exe`៖
   ```powershell
   python build_exe.py
   ```
3. ឯកសារ `AI_Movie_Dubbing_Studio.exe` នឹងត្រូវបង្កើតក្នុងថត `dist/AI_Movie_Dubbing_Studio/`។
4. ពេលនេះ អ្នកអាច copy ឯកសារ `.exe` នេះទៅកាន់កុំព្យូទ័រ PC ផ្សេងៗបានហើយ។ រាល់ពេលដែលគេបើកកម្មវិធី វានឹងទាក់ទងមកកាន់ Render Cloud Server របស់អ្នកដោយស្វ័យប្រវត្តិតាមរយៈ Internet គ្រប់ទីកន្លែងទូទាំងពិភពលោក!
