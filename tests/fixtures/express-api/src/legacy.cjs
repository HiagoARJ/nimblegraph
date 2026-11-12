const express = require('express')
const { requireAuth } = require('./middlewares/auth')
const health = express.Router()
health.get('/health', requireAuth, (req, res) => res.send('ok'))
module.exports = health
