import React, { useState } from 'react';
import { FaCloudUploadAlt } from 'react-icons/fa';

const FileUpload = ({ onUpload }) => {
  const [dragActive, setDragActive] = useState(false);

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      onUpload(e.dataTransfer.files[0]);
    }
  };

  const handleChange = (e) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      onUpload(e.target.files[0]);
    }
  };

  return (
    <div
      className={`relative p-8 border-2 border-dashed rounded-lg text-center cursor-pointer transition-colors ${
        dragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400'
      }`}
      onDragEnter={handleDrag}
      onDragLeave={handleDrag}
      onDragOver={handleDrag}
      onDrop={handleDrop}
    >
      <input
        type="file"
        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
        onChange={handleChange}
        accept=".pdf,.txt,.docx"
      />
      <div className="flex flex-col items-center justify-center space-y-2">
        <FaCloudUploadAlt className="text-4xl text-gray-400" />
        <p className="text-lg font-medium text-gray-700">
          Drag & drop your medical document here
        </p>
        <p className="text-sm text-gray-500">or click to browse</p>
        <p className="text-xs text-gray-400 mt-2">Supports PDF, TXT, DOCX</p>
      </div>
    </div>
  );
};

export default FileUpload;
