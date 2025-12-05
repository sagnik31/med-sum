import React, { useEffect, useState } from "react";
import { fetchUserInsights } from "../api/client";
import { Link } from "react-router-dom";

function UserInsightsPage() {
    const [insights, setInsights] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        loadInsights();
    }, []);

    async function loadInsights() {
        setLoading(true);
        setError("");
        try {
            const data = await fetchUserInsights();
            if (data && data.status === "completed" && data.insights_html) {
                setInsights(data.insights_html);
            } else {
                setInsights(null);
            }
        } catch (err) {
            console.error(err);
            setError(err.message || "Failed to load insights.");
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="bg-white rounded-xl shadow p-6">
            <div className="flex items-center justify-between mb-6">
                <h1 className="text-xl font-semibold">Patient Summary</h1>
                <Link
                    to="/documents"
                    className="text-sm text-slate-600 hover:text-slate-900 hover:underline"
                >
                    &larr; Back to Documents
                </Link>
            </div>

            {loading ? (
                <div className="text-center py-12">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-slate-900 mx-auto mb-4"></div>
                    <p className="text-slate-600">Loading patient summary...</p>
                </div>
            ) : error ? (
                <div className="bg-red-50 border border-red-100 rounded-md p-4 text-red-600">
                    {error}
                </div>
            ) : insights ? (
                <div
                    className="prose prose-slate max-w-none"
                    dangerouslySetInnerHTML={{ __html: insights }}
                />
            ) : (
                <div className="text-center py-12 bg-slate-50 rounded-lg border border-dashed border-slate-200">
                    <p className="text-slate-500 mb-4">
                        No patient summary generated yet.
                    </p>
                    {/* Future: Add Generate Button here */}
                </div>
            )}
        </div>
    );
}

export default UserInsightsPage;
