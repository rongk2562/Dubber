# ប្រព័ន្ធដំណឹងពី Developer (Remote Signed Notifications Guide)

## ១. ស្ថាបត្យកម្ម និងសុវត្ថិភាព
- ឯកសារ `notifications.json` ត្រូវបានផ្ទុកលើ GitHub / HTTPS static hosting (`NOTIFICATION_FEED_URL`)។
- រាល់ដំណឹងត្រូវចុះហត្ថលេខា **Ed25519** ទៅជា `notifications.sig` (`NOTIFICATION_SIG_URL`)។
- កម្មវិធី Client មាន **Public Key** (`NOTIFICATION_PUBLIC_KEY_B64` ក្នុង `app/config.py`) សម្រាប់ផ្ទៀងផ្ទាត់ហត្ថលេខា។ បើ server/repo ត្រូវបានកែប្រែដោយគ្មាន Private Key ហត្ថលេខានឹងមិនត្រូវគ្នា ហើយកម្មវិធីនឹងបដិសេធដោយស្ងាត់ៗ។
- **Private Key** (`tools/keys/dev_private.pem`) ត្រូវបានដាក់ក្នុង `.gitignore` — **ហាម commit ឬចែករំលែក Private Key ជាដាច់ខាត**។

---

## ២. ការបង្កើត Key Pair ថ្មី (បើត្រូវការប្ដូរ Key)
```bash
py tools/publish_notice.py --gen-keys
```
1. វាបង្កើត `tools/keys/dev_private.pem`។
2. វាបោះពុម្ព Base64 Public Key នៅលើ Console។
3. ចម្លង Public Key នោះទៅជំនួស `NOTIFICATION_PUBLIC_KEY_B64` ក្នុង `app/config.py` រួច build `.exe` ថ្មី។

---

## ៣. ការបង្ហោះដំណឹងថ្មីទៅអ្នកប្រើប្រាស់ទាំងអស់
```bash
py tools/publish_notice.py
```
1. បំពេញប្រភេទ (`news`, `update`, `warning`, `promo`, `critical`), ចំណងជើង, ខ្លឹមសារ (ខ្មែរ/អង់គ្លេស), ថ្ងៃផុតកំណត់, និង link (domain ក្នុង `NOTIFICATION_LINK_ALLOWLIST`)។
2. Script នឹងបន្ថែមដំណឹងចូល `notifications.json` និងចុះហត្ថលេខាចូល `notifications.sig` ដោយស្វ័យប្រវត្តិ។
3. ជ្រើស `y` ដើម្បី Commit & Push ទៅ GitHub (ឬ upload `notifications.json` + `notifications.sig` ទៅ server របស់អ្នក)។

ប្រសិនបើអ្នកកែ `notifications.json` ដោយដៃ កុំភ្លេចរត់ពាក្យបញ្ជា៖
```bash
py tools/publish_notice.py --sign-only
```
