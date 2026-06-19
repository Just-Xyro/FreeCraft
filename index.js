const express = require("express");
const path = require("path");
require("dotenv").config();

const app = express();
const PORT = process.env.PORT || 5000;

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public'), {
  setHeaders: (res) => {
    res.set('Cache-Control', 'no-cache, no-store, must-revalidate');
  }
}));

app.get("/", (req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'FreeCraft' });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`FreeCraft — Free Minecraft Marketplace`);
  console.log(`Frontend running at http://0.0.0.0:${PORT}`);
});
