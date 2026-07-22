# Troubleshooting Guide

## Common Issues

### Payment Failures
- **Insufficient funds**: The customer's bank declined the transaction. Ask the customer to verify their account balance or try a different payment method.
- **Gateway timeout**: The payment gateway may be experiencing high latency. Retry the transaction after a few minutes. If the issue persists, contact the gateway support.
- **Invalid card details**: Ensure the card number, expiration date, and CVV are correct. Check for typos or expired cards.

### API Errors
- **401 Unauthorized**: The API key is missing or invalid. Regenerate the API key and update the client configuration.
- **404 Not Found**: The requested endpoint does not exist. Verify the URL and API version.
- **500 Internal Server Error**: A server-side error occurred. Check the server logs for details and restart the service if necessary.

### Performance Issues
- **Slow dashboard**: Optimize database queries by adding indexes on frequently queried columns. Reduce the number of displayed records per page.
- **Timeout errors**: Increase the timeout limit in the client configuration or optimize the server response time.

### Login Problems
- **Forgot password**: Use the 'Forgot Password' feature to reset credentials.
- **Account locked**: After multiple failed attempts, the account may be temporarily locked. Wait for the lock period to expire or contact support.

For further assistance, search the knowledge base for more detailed articles.
