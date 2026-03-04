# snflwr.ai Load Testing Suite

Comprehensive load testing for snflwr.ai using k6. Validates production readiness claims of 100 concurrent users and 1000+ messages/minute.

## Prerequisites

Install k6:
```bash
# macOS
brew install k6

# Ubuntu/Debian
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update
sudo apt-get install k6

# Windows (via Chocolatey)
choco install k6

# Docker
docker pull grafana/k6
```

## Running Load Tests

### Basic Test (Default Configuration)
```bash
k6 run tests/load/snflwr_load_test.js
```

This runs the full load test with the following stages:
- **1 min**: Ramp up to 20 users
- **2 min**: Ramp up to 50 users
- **2 min**: Ramp up to 100 users
- **3 min**: Sustain 100 users (peak load)
- **1 min**: Ramp down to 50 users
- **1 min**: Ramp down to 0 users

**Total duration**: ~10 minutes

### Custom Configuration

#### Quick Smoke Test (2 minutes, 10 users)
```bash
k6 run --vus 10 --duration 2m tests/load/snflwr_load_test.js
```

#### Stress Test (5 minutes, 200 users)
```bash
k6 run --vus 200 --duration 5m tests/load/snflwr_load_test.js
```

#### Spike Test (Sudden traffic surge)
```bash
k6 run --stage 0s:0,1m:100,2m:100,3m:0 tests/load/snflwr_load_test.js
```

### Environment Variables

```bash
# Custom API endpoint
BASE_URL=http://production.snflwr.ai:8000 k6 run tests/load/snflwr_load_test.js

# Custom API key
API_KEY=your-api-key-here k6 run tests/load/snflwr_load_test.js
```

## Performance Thresholds

The test enforces the following performance thresholds:

### API Response Times
- **p95 < 2000ms**: 95% of API requests complete within 2 seconds
- **p99 < 5000ms**: 99% of API requests complete within 5 seconds

### Message Processing
- **p95 < 3000ms**: 95% of messages processed (including safety checks) within 3 seconds
- **p99 < 7000ms**: 99% of messages processed within 7 seconds

### Safety Pipeline
- **p95 < 1000ms**: 95% of safety checks complete within 1 second
- **p99 < 2000ms**: 99% of safety checks complete within 2 seconds

### Database Queries
- **p95 < 500ms**: 95% of database queries complete within 500ms
- **p99 < 1000ms**: 99% of database queries complete within 1 second

### Error Rates
- **HTTP failures < 5%**: Less than 5% of HTTP requests fail
- **Overall failure rate < 5%**: Less than 5% of operations fail

## Test Scenarios

Each virtual user (VU) performs the following workflow:

1. **Authenticate** - Login and obtain session token
2. **Send Chat Message** - Send message through full safety pipeline
3. **Get Profile** - Retrieve child profile information
4. **Check Incidents** - Query safety incidents
5. **Get Conversations** - Retrieve conversation history
6. **Think Time** - 1-5 second pause (simulating human behavior)

This workflow repeats continuously during the test duration.

## Interpreting Results

### Success Criteria

The load test **PASSES** if:
- ✅ All thresholds are met (shown in green)
- ✅ Messages per minute ≥ 1000
- ✅ HTTP failure rate < 5%
- ✅ No timeout errors

### Example Successful Output
```
✓ http_req_duration.............: avg=1245ms min=203ms med=982ms max=4821ms p(95)=1892ms p(99)=3145ms
✓ message_processing_time.......: avg=2103ms min=812ms med=1945ms max=6234ms p(95)=2845ms p(99)=4932ms
✓ safety_check_time.............: avg=687ms min=152ms med=623ms max=1823ms p(95)=923ms p(99)=1456ms
✓ db_query_time.................: avg=234ms min=45ms med=198ms max=923ms p(95)=412ms p(99)=678ms
✓ http_req_failed...............: 2.34% (< 5% threshold)
✓ messages_processed............: 11,234 (1123/min)
✓ incidents_detected............: 89
```

### Common Issues

#### High Response Times (p95 > 2000ms)
**Possible Causes**:
- Insufficient CPU/RAM
- Database not indexed properly
- Too many concurrent LLM requests
- Network latency

**Solutions**:
- Scale horizontally (add more workers)
- Add database indexes
- Implement request queuing
- Use connection pooling

#### High Failure Rate (> 5%)
**Possible Causes**:
- Database connection pool exhausted
- API rate limiting
- Authentication issues
- Timeout errors

**Solutions**:
- Increase connection pool size
- Add retry logic with exponential backoff
- Check authentication flow
- Increase timeout thresholds

#### Low Messages/Minute (< 1000)
**Possible Causes**:
- Safety pipeline bottleneck
- Ollama processing too slow
- Database write contention
- Single-threaded bottleneck

**Solutions**:
- Optimize safety checks (caching, batch processing)
- Use faster Ollama models for safety checks
- Implement write batching
- Use async processing where possible

## Advanced Usage

### Generate HTML Report
```bash
k6 run --out json=results.json tests/load/snflwr_load_test.js
k6 report results.json --output report.html
```

### Stream Metrics to Grafana Cloud
```bash
K6_CLOUD_TOKEN=your-token k6 cloud tests/load/snflwr_load_test.js
```

### Export to Prometheus
```bash
k6 run --out prometheus tests/load/snflwr_load_test.js
```

### Run with Docker
```bash
docker run --rm -i grafana/k6 run - <tests/load/snflwr_load_test.js
```

## Continuous Integration

### GitHub Actions Example
```yaml
name: Load Test

on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly on Sunday at 2am
  workflow_dispatch:

jobs:
  load-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Install k6
        run: |
          sudo apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
          echo "deb https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
          sudo apt-get update
          sudo apt-get install k6

      - name: Run load test
        run: k6 run tests/load/snflwr_load_test.js
        env:
          BASE_URL: http://localhost:8000

      - name: Upload results
        uses: actions/upload-artifact@v3
        if: always()
        with:
          name: load-test-results
          path: summary.json
```

## Metrics Collected

### HTTP Metrics
- `http_req_duration`: Total request duration
- `http_req_waiting`: Time waiting for response
- `http_req_sending`: Time sending request
- `http_req_receiving`: Time receiving response
- `http_req_failed`: Failed requests rate

### Custom Metrics
- `message_processing_time`: End-to-end message processing duration
- `safety_check_time`: Safety pipeline processing duration
- `db_query_time`: Database query duration
- `messages_processed`: Total messages processed
- `incidents_detected`: Total safety incidents detected
- `failure_rate`: Overall failure rate

## Horizontal Scaling Verification

To verify horizontal scaling claims:

1. **Run baseline test with single instance**:
   ```bash
   k6 run --vus 50 --duration 5m tests/load/snflwr_load_test.js
   # Note the messages/minute rate
   ```

2. **Scale to 2 instances** (e.g., via Docker Compose or Kubernetes)

3. **Run test again**:
   ```bash
   k6 run --vus 100 --duration 5m tests/load/snflwr_load_test.js
   # Messages/minute should be ~2x baseline
   ```

4. **Expected results**:
   - 1 instance: ~600 messages/minute
   - 2 instances: ~1200 messages/minute
   - Linear scaling demonstrates horizontal scalability

## Troubleshooting

### Test fails to start
```bash
# Check API is running
curl http://localhost:8000/api/health

# Check k6 version
k6 version
```

### Connection refused errors
```bash
# Verify BASE_URL
BASE_URL=http://localhost:8000 k6 run tests/load/snflwr_load_test.js

# Check firewall rules
sudo ufw status
```

### Out of memory
```bash
# Reduce VUs or duration
k6 run --vus 25 --duration 2m tests/load/snflwr_load_test.js
```

## References

- [k6 Documentation](https://k6.io/docs/)
- [k6 Thresholds](https://k6.io/docs/using-k6/thresholds/)
- [Load Testing Best Practices](https://k6.io/docs/testing-guides/load-testing-websites/)
