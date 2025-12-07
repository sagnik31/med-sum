--
-- PostgreSQL database dump
--

\restrict ivrPuJMVxaPgpBCgI3R6pW0NrBVOzpeIyTQYGqYab23nU2DJraJBgrWBQHoopQY

-- Dumped from database version 18.1
-- Dumped by pg_dump version 18.1

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: document_processing_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.document_processing_status AS ENUM (
    'pending',
    'extracting',
    'insights_generating',
    'done',
    'failed'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    original_name text NOT NULL,
    content_type text NOT NULL,
    storage_path text NOT NULL,
    status text DEFAULT 'uploaded'::text NOT NULL,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    extracted_markdown text,
    processing_status public.document_processing_status DEFAULT 'pending'::public.document_processing_status NOT NULL,
    last_error text
);


--
-- Name: insights; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insights (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    summary_text text,
    details_json jsonb,
    error_message text,
    generated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    html_insights text,
    user_id uuid
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    phone_number text NOT NULL,
    full_name text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login_at timestamp with time zone,
    patient_insights text
);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: insights insights_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insights
    ADD CONSTRAINT insights_pkey PRIMARY KEY (id);


--
-- Name: users users_phone_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_phone_number_key UNIQUE (phone_number);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_documents_processing_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_processing_status ON public.documents USING btree (processing_status);


--
-- Name: idx_documents_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_user_id ON public.documents USING btree (user_id);


--
-- Name: idx_insights_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_insights_document_id ON public.insights USING btree (document_id);


--
-- Name: idx_insights_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_insights_status ON public.insights USING btree (status);


--
-- Name: uq_insights_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_insights_document_id ON public.insights USING btree (document_id);


--
-- Name: documents documents_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: insights insights_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insights
    ADD CONSTRAINT insights_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict ivrPuJMVxaPgpBCgI3R6pW0NrBVOzpeIyTQYGqYab23nU2DJraJBgrWBQHoopQY

