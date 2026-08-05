import { useState, useCallback, useEffect } from 'react';
import { BrowserProvider, JsonRpcProvider, Contract } from 'ethers';
import { ASSETS, NETWORKS } from '../constants/contracts';

const ERC20_ABI = [
  'function balanceOf(address owner) view returns (uint256)',
  'function decimals() view returns (uint8)',
];

const ETH_PROVIDER = new JsonRpcProvider(NETWORKS.ETHEREUM.rpcUrl);
const POLY_PROVIDER = new JsonRpcProvider(NETWORKS.POLYGON.rpcUrl);

function getProvider(chain) {
  return chain === 'polygon' ? POLY_PROVIDER : ETH_PROVIDER;
}

export function useWallet() {
  const [account, setAccount] = useState(null);
  const [balances, setBalances] = useState({});
  const [chainId, setChainId] = useState(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState(null);

  const connect = useCallback(async () => {
    if (!window.ethereum) {
      setError('MetaMask not detected. Please install MetaMask.');
      return;
    }
    setIsConnecting(true);
    setError(null);
    try {
      const provider = new BrowserProvider(window.ethereum);
      const accounts = await provider.send('eth_requestAccounts', []);
      const network = await provider.getNetwork();
      setAccount(accounts[0]);
      setChainId(Number(network.chainId));
    } catch (err) {
      setError(err.message || 'Failed to connect');
    } finally {
      setIsConnecting(false);
    }
  }, []);

  const switchNetwork = useCallback(async (networkKey) => {
    if (!window.ethereum) return;
    const net = NETWORKS[networkKey === 'ethereum' ? 'ETHEREUM' : 'POLYGON'];
    try {
      await window.ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: net.chainId }],
      });
    } catch (err) {
      if (err.code === 4902) {
        await window.ethereum.request({
          method: 'wallet_addEthereumChain',
          params: [{
            chainId: net.chainId,
            chainName: net.name,
            rpcUrls: [net.rpcUrl],
          }],
        });
      }
    }
  }, []);

  const fetchBalances = useCallback(async () => {
    if (!account) return;
    const newBalances = {};
    for (const asset of ASSETS) {
      try {
        const provider = getProvider(asset.chain);
        if (asset.address) {
          const contract = new Contract(asset.address, ERC20_ABI, provider);
          const bal = await contract.balanceOf(account);
          newBalances[asset.id] = Number(bal) / 10 ** asset.decimals;
        } else {
          const bal = await provider.getBalance(account);
          newBalances[asset.id] = Number(bal) / 10 ** asset.decimals;
        }
      } catch {
        newBalances[asset.id] = 0;
      }
    }
    setBalances(newBalances);
  }, [account]);

  useEffect(() => {
    if (!account) return;
    fetchBalances();
    const interval = setInterval(fetchBalances, 15000);
    return () => clearInterval(interval);
  }, [account, fetchBalances]);

  useEffect(() => {
    if (!window.ethereum) return;
    const onAccountsChanged = (accounts) => setAccount(accounts[0] || null);
    const onChainChanged = () => window.location.reload();
    window.ethereum.on('accountsChanged', onAccountsChanged);
    window.ethereum.on('chainChanged', onChainChanged);
    return () => {
      window.ethereum?.removeListener('accountsChanged', onAccountsChanged);
      window.ethereum?.removeListener('chainChanged', onChainChanged);
    };
  }, []);

  return { account, balances, chainId, connect, switchNetwork, fetchBalances, isConnecting, error };
}
