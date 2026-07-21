// EvaluatorRouter ABI — registerJob (bind policy) + settle (approve escrow release)
export const ROUTER_ABI = [
  {
    "inputs": [
      { "internalType": "uint256", "name": "jobId", "type": "uint256" },
      { "internalType": "address", "name": "policy", "type": "address" }
    ],
    "name": "registerJob",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [
      { "internalType": "uint256", "name": "jobId", "type": "uint256" },
      { "internalType": "bytes", "name": "evidence", "type": "bytes" }
    ],
    "name": "settle",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  }
] as const;
