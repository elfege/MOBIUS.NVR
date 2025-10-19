# UniFi G5-Flex Camera Proxy Docker Stack

A containerized solution for integrating UniFi G5-Flex cameras with Blue Iris surveillance software, bypassing the session authentication requirements.

## Overview

The UniFi G5-Flex cameras require session-based authentication which Blue Iris cannot handle natively. This proxy stack maintains the session with  cameras and serves standard MJPEG streams that Blue Iris can consume without authentication.

## Features

- **Session Management**: Automatic login and session renewal
- **MJPEG Streaming**: Blue Iris compatible video streams  
- **Multi-Camera Support**: Easy scaling for multiple cameras
- **Health Monitoring**: Built-in health checks and statistics
- **Reverse Proxy**: Nginx for load balancing and SSL termination
- **Monitoring Stack**: Optional Grafana/Prometheus monitoring
- **Auto-Updates**: Optional Watchtower for automatic updates

## Quick Start

1. **Setup the environment:**
   ```bash
   chmod +x deploy.sh
   ./deploy.sh setup
   ```

2. **Edit configuration:**
   ```bash
   nano .env
   ```
   Update `CAMERA_IP` and credentials as needed.

3. **Deploy the stack:**
   ```bash
   ./deploy.sh deploy
   ```

4. **Configure Blue Iris:**
   - Camera Type: HTTP JPEG/MJPEG
   - Make: Axis
   - Model: 200+ MJPEG
   - URL: `http://YOUR_SERVER_IP:8080/g5flex.mjpeg`
   - Authentication: None

## Directory Structure

```
UBIQUITI_NVR/
├── docker-compose.yml       # Main compose file
├── Dockerfile              # Camera proxy container
├── stream_proxy.py         # Enhanced proxy script
├── deploy.sh              # Management script
├── .env                   # Environment variables
├── logs/                  # Application logs
├── nginx/                 # Nginx configuration
├── monitoring/            # Grafana/Prometheus configs
└── README.md             # This file
```

## Configuration

Key environment variables in `.env`:

```bash
# Camera settings
CAMERA_IP=192.168.10.104
CAMERA_USERNAME=ubnt
CAMERA_PASSWORD=ubnt

# Proxy settings  
PROXY_PORT=8080
FRAME_RATE=2.0
SESSION_TIMEOUT=3600
LOG_LEVEL=INFO
```

## Management Commands

```bash
# Basic operations
./deploy.sh start           # Start services
./deploy.sh stop            # Stop services  
./deploy.sh restart         # Restart services
./deploy.sh status          # Show status

# Monitoring
./deploy.sh logs            # Show all logs
./deploy.sh logs g5flex-proxy  # Show specific service logs

# Maintenance
./deploy.sh update          # Update stack
./deploy.sh backup          # Backup configuration
./deploy.sh clean           # Clean up everything
```

## Scaling for Multiple Cameras

1. **Add camera service to docker-compose.yml:**
   ```yaml
   g5flex-proxy-2:
     build: .
     ports:
       - "8081:8080"
     environment:
       - CAMERA_IP=192.168.10.105
   ```

2. **Update nginx configuration:**
   ```nginx
   upstream g5flex-2 {
       server g5flex-proxy-2:8080;
   }
   ```

3. **Configure Blue Iris:**
   - URL: `http://YOUR_SERVER_IP:8081/g5flex.mjpeg`

## Monitoring (Optional)

Deploy with monitoring stack:
```bash
./deploy.sh deploy-monitoring
```

Access points:
- **Grafana**: http://YOUR_SERVER_IP:3000 (admin/admin)
- **Prometheus**: http://YOUR_SERVER_IP:9090

## API Endpoints

Each camera proxy provides:

- `/g5flex.mjpeg` - MJPEG stream for Blue Iris
- `/g5flex.jpeg` - Single snapshot
- `/health` - Health check endpoint
- `/stats` - Statistics and metrics
- `/` - Status page

## Troubleshooting

**Container won't start:**
```bash
./deploy.sh logs g5flex-proxy
```

**Camera connection issues:**
```bash
curl http://localhost:8080/health
curl http://localhost:8080/stats
```

**Port conflicts:**
```bash
sudo netstat -tlnp | grep 8080
```

**Reset everything:**
```bash
./deploy.sh clean
./deploy.sh setup
./deploy.sh deploy
```

## Security Considerations

- Change default camera credentials
- Use internal networks for camera communication
- Consider SSL termination with nginx
- Regularly update containers with Watchtower

## Performance Tuning

- Adjust `FRAME_RATE` for bandwidth/quality balance
- Monitor resource usage with `./deploy.sh status`
- Scale horizontally by adding more proxy instances
- Use nginx load balancing for high availability

## Dependencies

- Docker Engine 20.10+
- Docker Compose 2.0+
- UniFi G5-Flex camera with firmware 4.59.32+
- Network connectivity to camera

## License

This is a custom solution for integrating UniFi cameras with Blue Iris. Use at  own discreti