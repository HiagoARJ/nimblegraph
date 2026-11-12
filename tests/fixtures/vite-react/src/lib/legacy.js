// plain JS with JSX and CommonJS
const { cn } = require('./cn')
const Banner = (p) => <div className={cn('banner')}>{p.text}</div>
module.exports = { Banner, cn }
